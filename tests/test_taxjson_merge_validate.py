"""Tests for taxjson-merge and taxjson-validate.

Both are simple tools but they live on the critical path: every brokerage
output passes through merge, and validate is the user's last line of defense
before bad data hits the gains engine.
"""

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch


class TestTaxjsonMerge(unittest.TestCase):
    def _write(self, transactions, metadata=None):
        fh = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        doc = {'transactions': transactions}
        if metadata:
            doc['metadata'] = metadata
        json.dump(doc, fh)
        fh.close()
        return fh.name

    def _run_merge(self, files):
        from taxjson.bin.taxjson_merge import main
        captured = StringIO()
        with patch.object(sys, 'argv', ['taxjson-merge'] + files):
            with patch.object(sys, 'stdout', captured):
                main()
        return json.loads(captured.getvalue())

    def test_concatenates_two_files(self):
        a = self._write([{'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'X'}])
        b = self._write([{'action': 'BUYSELL', 'date': '2025-02-15', 'symbol': 'Y'}])
        result = self._run_merge([a, b])
        self.assertEqual(len(result['transactions']), 2)
        symbols = {t['symbol'] for t in result['transactions']}
        self.assertEqual(symbols, {'X', 'Y'})

    def test_preserves_duplicate_records_for_dedup_downstream(self):
        """merge does NOT dedup — that's the job of taxjson-sort --dedup.
        Including the same record twice should produce 2 entries."""
        a = self._write([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'X', 'id': 'abc'},
            {'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'X', 'id': 'abc'},
        ])
        result = self._run_merge([a])
        self.assertEqual(len(result['transactions']), 2)

    def test_metadata_records_sources(self):
        a = self._write(
            [{'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'X'}],
            metadata={'source_brokerage': 'rbc_direct'},
        )
        result = self._run_merge([a])
        self.assertIn('metadata', result)
        self.assertEqual(len(result['metadata']['sources']), 1)
        self.assertEqual(
            result['metadata']['sources'][0]['original_metadata']['source_brokerage'],
            'rbc_direct',
        )
        self.assertEqual(result['metadata']['sources'][0]['count'], 1)

    def test_empty_files_merge_cleanly(self):
        a = self._write([])
        b = self._write([])
        result = self._run_merge([a, b])
        self.assertEqual(result['transactions'], [])

    def test_three_way_merge_preserves_order(self):
        a = self._write([{'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'A'}])
        b = self._write([{'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'B'}])
        c = self._write([{'action': 'BUYSELL', 'date': '2025-01-15', 'symbol': 'C'}])
        result = self._run_merge([a, b, c])
        symbols_in_order = [t['symbol'] for t in result['transactions']]
        self.assertEqual(symbols_in_order, ['A', 'B', 'C'])


class TestTaxjsonValidate(unittest.TestCase):
    """Validate flags bad data before it reaches the engine. We test the
    function-level validator directly (cleaner than invoking the CLI)."""

    def _validate(self, transactions):
        from taxjson.bin.taxjson_validate import validate_transactions
        return validate_transactions(transactions, filename='test.json')

    def test_clean_data_has_no_issues(self):
        issues, warnings = self._validate([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
             'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD'},
        ])
        self.assertEqual(len(issues), 0)

    def test_missing_action_flagged(self):
        issues, _ = self._validate([
            {'date': '2025-01-15', 'symbol': 'X', 'quantity': 100},
        ])
        self.assertEqual(len(issues), 1)
        only_context = next(iter(issues.values()))
        self.assertTrue(any('action' in msg.lower() for msg in only_context))

    def test_malformed_date_flagged(self):
        issues, _ = self._validate([
            {'action': 'BUYSELL', 'date': '01/15/2025', 'symbol': 'X', 'quantity': 100},
        ])
        only_context = next(iter(issues.values()))
        self.assertTrue(any('Malformed date' in msg for msg in only_context))

    def test_unknown_action_warned_not_errored(self):
        """Unknown action triggers a warning but doesn't block as an issue.
        (Currency is required and present here, so any non-empty issue list
        is attributable to the action itself.)"""
        issues, warnings = self._validate([
            {'action': 'MYSTERY_ACTION', 'date': '2025-01-15', 'symbol': 'X',
             'quantity': 100, 'currency': 'USD'},
        ])
        # Warning should mention the unknown action.
        only_w = next(iter(warnings.values()))
        self.assertTrue(any('Unknown action' in msg for msg in only_w))
        # And nothing about the action itself should land in issues —
        # warnings are the right severity for "we don't recognize this."
        for ctx, msgs in issues.items():
            for msg in msgs:
                self.assertNotIn('action', msg.lower(),
                                 msg=f"Action complaint leaked to issues: {msg}")

    def test_buysell_without_symbol_flagged(self):
        issues, _ = self._validate([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'quantity': 100},
        ])
        only_context = next(iter(issues.values()))
        self.assertTrue(any('symbol' in msg.lower() for msg in only_context))

    def test_malformed_time_warns(self):
        _, warnings = self._validate([
            {'action': 'BUYSELL', 'date': '2025-01-15', 'time': '9:30',
             'symbol': 'X', 'quantity': 100},
        ])
        only_context = next(iter(warnings.values()))
        self.assertTrue(any('Malformed time' in msg for msg in only_context))


if __name__ == '__main__':
    unittest.main()

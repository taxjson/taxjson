"""Tests for the taxjson-diff tool.

This is a behavioral test of the diff *logic* — invoking the diff at the
function level. It verifies match-by-composite-key (not by id), float
tolerance, duplicate handling, custom match fields, and section counts.
"""

import json
import tempfile
import unittest
from pathlib import Path

# Import the helpers directly from the script module.
from taxjson.bin.taxjson_diff import (
    extract_records, field_diffs, make_key, values_equal,
)


class TestValuesEqual(unittest.TestCase):
    def test_floats_within_tolerance(self):
        self.assertTrue(values_equal(1.0, 1.0000001))

    def test_floats_outside_tolerance(self):
        self.assertFalse(values_equal(1.0, 1.001))

    def test_none_equals_empty_string(self):
        self.assertTrue(values_equal(None, ''))
        self.assertTrue(values_equal('', None))

    def test_string_equality(self):
        self.assertTrue(values_equal('USD', 'USD'))
        self.assertFalse(values_equal('USD', 'CAD'))


class TestMakeKey(unittest.TestCase):
    def test_rounds_floats(self):
        # make_key rounds floats to 6 decimal places, so values that agree
        # past that precision collapse to the same key.
        rec1 = {'symbol': 'X', 'price': 100.0000001}
        rec2 = {'symbol': 'X', 'price': 100.0000002}
        self.assertEqual(
            make_key(rec1, ['symbol', 'price']),
            make_key(rec2, ['symbol', 'price']),
        )

    def test_distinguishes_different_keys(self):
        rec1 = {'symbol': 'X', 'date': '2025-01-01'}
        rec2 = {'symbol': 'X', 'date': '2025-01-02'}
        self.assertNotEqual(
            make_key(rec1, ['symbol', 'date']),
            make_key(rec2, ['symbol', 'date']),
        )


class TestFieldDiffs(unittest.TestCase):
    def test_finds_modified_fields(self):
        old = {'a': 1.0, 'b': 'x', 'c': 5.0}
        new = {'a': 1.0, 'b': 'y', 'c': 5.5}
        diffs = field_diffs(old, new, ignore=set())
        keys = {d[0] for d in diffs}
        self.assertEqual(keys, {'b', 'c'})

    def test_respects_ignore_set(self):
        old = {'id': 'abc', 'val': 1}
        new = {'id': 'xyz', 'val': 1}
        self.assertEqual(field_diffs(old, new, ignore={'id'}), [])


class TestExtractRecords(unittest.TestCase):
    def test_dict_with_transactions(self):
        doc = {'transactions': [{'a': 1}, {'a': 2}]}
        self.assertEqual(len(extract_records(doc)), 2)

    def test_bare_list(self):
        self.assertEqual(extract_records([{'a': 1}]), [{'a': 1}])

    def test_missing_transactions_key(self):
        self.assertEqual(extract_records({'other': []}), [])


class TestDiffEndToEnd(unittest.TestCase):
    """Simulate the diff main-flow grouping logic by reproducing it inline.
    This keeps the test cheap (no subprocess) while exercising the
    bucket-and-compare core."""

    @staticmethod
    def _diff(old_recs, new_recs, match_fields, ignore=None):
        ignore = ignore or {'id'}
        old_buckets, new_buckets = {}, {}
        for r in old_recs:
            old_buckets.setdefault(make_key(r, match_fields), []).append(r)
        for r in new_recs:
            new_buckets.setdefault(make_key(r, match_fields), []).append(r)
        added, removed, modified = [], [], []
        for k in set(old_buckets) | set(new_buckets):
            olds, news = old_buckets.get(k, []), new_buckets.get(k, [])
            n_match = min(len(olds), len(news))
            for i in range(n_match):
                d = field_diffs(olds[i], news[i], ignore)
                if d:
                    modified.append((olds[i], news[i], d))
            removed.extend(olds[n_match:])
            added.extend(news[n_match:])
        return added, removed, modified

    def test_identical_inputs_no_diffs(self):
        recs = [{'symbol': 'X', 'date': '2025-01-01', 'val': 1.0, 'id': 'a'},
                {'symbol': 'X', 'date': '2025-01-02', 'val': 2.0, 'id': 'b'}]
        added, removed, modified = self._diff(recs, recs, ['symbol', 'date'])
        self.assertEqual((len(added), len(removed), len(modified)), (0, 0, 0))

    def test_pure_addition(self):
        old = [{'symbol': 'X', 'date': '2025-01-01', 'val': 1.0}]
        new = old + [{'symbol': 'Y', 'date': '2025-01-02', 'val': 2.0}]
        added, removed, modified = self._diff(old, new, ['symbol', 'date'])
        self.assertEqual((len(added), len(removed), len(modified)), (1, 0, 0))
        self.assertEqual(added[0]['symbol'], 'Y')

    def test_pure_removal(self):
        old = [{'symbol': 'X', 'date': '2025-01-01', 'val': 1.0},
               {'symbol': 'Y', 'date': '2025-01-02', 'val': 2.0}]
        new = old[:1]
        added, removed, modified = self._diff(old, new, ['symbol', 'date'])
        self.assertEqual((len(added), len(removed), len(modified)), (0, 1, 0))
        self.assertEqual(removed[0]['symbol'], 'Y')

    def test_modification_with_id_ignored(self):
        """Common case: a parser fix changes commission. Composite key
        match still finds the same trade; field diff shows the change."""
        old = [{'symbol': 'X', 'date': '2025-01-01', 'commission': 0.0,
                'net_amount': 5000.0, 'id': 'old123'}]
        new = [{'symbol': 'X', 'date': '2025-01-01', 'commission': 9.95,
                'net_amount': 5009.95, 'id': 'new456'}]
        added, removed, modified = self._diff(old, new, ['symbol', 'date'])
        self.assertEqual((len(added), len(removed), len(modified)), (0, 0, 1))
        diff_fields = {d[0] for d in modified[0][2]}
        self.assertIn('commission', diff_fields)
        self.assertIn('net_amount', diff_fields)
        # `id` is in the default ignore set — should NOT appear.
        self.assertNotIn('id', diff_fields)

    def test_duplicates_count_correctly(self):
        """Two identical trades on the same day → match 2-for-2; a third on
        either side shows as added/removed."""
        old = [
            {'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 50.0},
            {'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 50.0},
        ]
        new = [
            {'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 50.0},
            {'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 50.0},
            {'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 50.0},
        ]
        added, removed, modified = self._diff(
            old, new, ['symbol', 'date', 'qty', 'price'],
        )
        self.assertEqual((len(added), len(removed), len(modified)), (1, 0, 0))

    def test_float_noise_does_not_show_as_diff(self):
        """The 1e-6 tolerance lets parser float-roundtrip noise pass."""
        old = [{'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 99.9999999}]
        new = [{'symbol': 'X', 'date': '2025-01-01', 'qty': 100, 'price': 100.0}]
        added, removed, modified = self._diff(old, new, ['symbol', 'date', 'qty'])
        self.assertEqual((len(added), len(removed), len(modified)), (0, 0, 0))


class TestMissingInputIsClearError(unittest.TestCase):
    """2026-09 audit: a missing file surfaced as a raw FileNotFoundError
    traceback. It is a usage error: name the file, exit 2."""

    def _run(self, *args):
        import subprocess
        import sys
        repo = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_diff", *args],
            cwd=repo, capture_output=True, text=True)

    def test_missing_file_exit_2_no_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "a.json"
            good.write_text(json.dumps({"transactions": []}))
            r = self._run(str(Path(td) / "nope.json"), str(good))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cannot read", r.stderr)
        self.assertIn("nope.json", r.stderr)

    def test_malformed_file_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "a.json"
            good.write_text(json.dumps({"transactions": []}))
            bad = Path(td) / "b.json"
            bad.write_text("{not json")
            r = self._run(str(good), str(bad))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == '__main__':
    unittest.main()

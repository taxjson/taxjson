"""Tests for `taxjson-merge2`.

The combined pipeline tool (merge + sort/dedup + ticker-map + currency-
convert + validate) had no tests when it shipped. These cover the
behavioral contracts that are easy to regress on without noticing:

  1. `--validate-strict` must abort BEFORE writing stdout. Otherwise a
     shell redirect captures bad JSON alongside the nonzero exit and a
     CI step that only checks $? happily publishes the file.

  2. `--validate` (non-strict) prints to stderr but still emits the JSON.

  3. Sort/dedup ordering: dedup must run AFTER sort so the survivor is
     the chronologically-earliest row, not whichever happened to be
     first in the concatenated input.

  4. Missing input files warn-and-skip rather than failing the run —
     legacy `taxjson-merge` behavior we preserve so old shell scripts
     with stale file lists keep working.

Each test invokes the CLI via subprocess so the argparse layer and
stdout/stderr split are exercised exactly as a user would see them.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_json(path: Path, transactions):
    path.write_text(json.dumps({'transactions': transactions}), encoding='utf-8')


def _run_merge2(*args, env=None):
    """Run merge2 via `python -m` so we don't depend on the venv's
    console-script being on PATH. Returns CompletedProcess; we check
    stdout, stderr, returncode in the tests."""
    cmd = [sys.executable, '-m', 'taxjson.bin.taxjson_merge2', *args]
    real_env = os.environ.copy()
    if env:
        real_env.update(env)
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=real_env,
    )


class TestMerge2ValidateStrict(unittest.TestCase):
    def test_strict_aborts_emit_on_error(self):
        """A malformed row (missing currency) + --validate-strict must
        produce empty stdout and exit nonzero. Old behavior emitted the
        JSON then exit 1, so a `> out.json` redirect captured bad data."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bad = tmp / 'bad.json'
            # Missing 'currency' → validator flags it as an error.
            _write_json(bad, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'price': 150.0,
            }])
            result = _run_merge2('--validate-strict', str(bad))
            self.assertNotEqual(result.returncode, 0,
                                "validation errors must fail the run")
            self.assertEqual(result.stdout.strip(), '',
                             "no stdout should be emitted when --validate-strict aborts")
            self.assertIn('validation:', result.stderr)
            self.assertIn('aborted output emit', result.stderr)

    def test_validate_non_strict_still_emits(self):
        """Plain --validate is advisory; it prints errors to stderr but
        still emits the JSON so downstream stages can see what was
        actually produced. The contract is that --validate-strict is
        the only mode that gates output."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad.json'
            _write_json(bad, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'price': 150.0,
            }])
            result = _run_merge2('--validate', str(bad))
            self.assertEqual(result.returncode, 0)
            self.assertIn('validation:', result.stderr)
            # JSON still came through.
            data = json.loads(result.stdout)
            self.assertEqual(len(data['transactions']), 1)

    def test_strict_emits_when_no_errors(self):
        """Happy path: clean input + --validate-strict exits 0 with JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / 'good.json'
            _write_json(good, [{
                'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
                'symbol': 'AAPL.US', 'quantity': 100, 'price': 150.0,
                'net_amount': 15000.0, 'currency': 'USD', 'account': 'Margin',
            }])
            result = _run_merge2('--validate-strict', str(good))
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertEqual(len(data['transactions']), 1)


class TestMerge2TickerDrop(unittest.TestCase):
    def test_drop_line_removes_ticker(self):
        """A `<symbol> DROP` line in the ticker-map nukes that ticker's
        transactions; everything else passes through, and the deletion
        is announced on stderr."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_json(tmp / 'a.json', [
                {'action': 'BUYSELL', 'date': '2025-10-22',
                 'symbol': 'RGLD.CAD.TO', 'quantity': -0.0026, 'price': 0,
                 'net_amount': 0.64, 'currency': 'CAD', 'account': 'rrsp'},
                {'action': 'BUYSELL', 'date': '2025-10-22', 'symbol': 'NVDA.US',
                 'quantity': 100, 'price': 500, 'net_amount': 50000,
                 'currency': 'USD', 'account': 'rrsp'},
            ])
            (tmp / 'm.map').write_text('DELETE RGLD.CAD.TO\n')
            result = _run_merge2('--map', str(tmp / 'm.map'), str(tmp / 'a.json'))
            self.assertEqual(result.returncode, 0, result.stderr)
            syms = [t['symbol'] for t in json.loads(result.stdout)['transactions']]
            self.assertEqual(syms, ['NVDA.US'])
            self.assertIn('DROP removed 1 RGLD.CAD.TO', result.stderr)


class TestMerge2StageOrder(unittest.TestCase):
    def test_sort_runs_before_dedup(self):
        """If a duplicate appears earlier in the input than its
        chronologically-earlier twin, sort-then-dedup must keep the
        earlier-by-date row. Confirms merge2's ordering (sort runs
        before dedup) doesn't regress to the bare taxjson_sort.py
        behavior where dedup ran first."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # Two rows with the same id but different dates. The
            # second one in argv order is chronologically earlier.
            _write_json(tmp / 'a.json', [{
                'action': 'BUYSELL', 'date': '2025-03-01',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0, 'id': 'dup',
            }])
            _write_json(tmp / 'b.json', [{
                'action': 'BUYSELL', 'date': '2025-01-01',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0, 'id': 'dup',
            }])
            result = _run_merge2('--sort', '--dedup',
                                 str(tmp / 'a.json'), str(tmp / 'b.json'))
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            self.assertEqual(len(data['transactions']), 1)
            # Sort-before-dedup keeps the earliest date.
            self.assertEqual(data['transactions'][0]['date'], '2025-01-01')


class TestMerge2DefaultRateWarnings(unittest.TestCase):
    """The standalone `taxjson-convert-currency` CLI emits a stderr
    summary listing how many rows hit the hardcoded `--default-rate`.
    `taxjson-merge2` calls process_transactions directly and used to
    skip that summary, re-introducing the silent-default-rate hazard
    the tally was added to prevent. These tests pin the wiring."""

    def test_to_without_rates_warns_upfront(self):
        """Running --to USD without --rates must emit a loud upfront
        stderr warning, mirroring the standalone CLI's behavior. A
        user who forgot --rates would otherwise get every cross-
        currency row converted at 1.35 with no indication."""
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / 'in.json'
            _write_json(inp, [{
                'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
                'symbol': 'AAPL.US', 'quantity': 100, 'price': 150.0,
                'net_amount': 15000.0, 'currency': 'USD', 'account': 'Margin',
            }])
            result = _run_merge2('--to', 'CAD', str(inp))
            self.assertEqual(result.returncode, 0)
            self.assertIn('given without --rates', result.stderr)
            self.assertIn('--default-rate', result.stderr)

    def test_default_rate_summary_fires_on_missing_dates(self):
        """When a rates file is present but doesn't cover the tx date,
        the per-row tally must trigger the same summary the standalone
        CLI emits. Regression guard for the module-global state being
        cleared and read in merge2's main path."""
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / 'in.json'
            _write_json(inp, [{
                'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
                'symbol': 'AAPL.US', 'quantity': 100, 'price': 150.0,
                'net_amount': 15000.0, 'currency': 'USD', 'account': 'Margin',
            }])
            # Rates file covers a different currency — USD rows fall
            # back to --default-rate for every field conversion. An
            # entirely-uncovered currency is fatal unless the fallback
            # is opted into with an EXPLICIT --default-rate (stage-tools
            # audit; see test_stage_tools_audit), so pass it here.
            rates = Path(tmp) / 'rates.txt'
            rates.write_text(
                '2025-01-15 12:00:00 EUR CAD 1.5500\n',
                encoding='utf-8',
            )
            result = _run_merge2('--to', 'CAD', '--rates', str(rates),
                                 '--default-rate', '1.35', str(inp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--default-rate', result.stderr)
            self.assertIn('USD', result.stderr)


class TestMerge2PreservesOriginalMetadata(unittest.TestCase):
    """Legacy taxjson-merge records each source file's own metadata
    block in the `sources` list. merge2 dropped this until N5 landed;
    downstream consumers reading `metadata.sources[N].original_metadata`
    (e.g. tools surfacing the original brokerage id) silently lost it."""

    def test_source_metadata_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / 'a.json'
            a.write_text(json.dumps({
                'transactions': [{
                    'action': 'BUYSELL', 'date': '2025-01-15',
                    'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                }],
                'metadata': {'source_brokerage': 'ib'},
            }))
            result = _run_merge2(str(a))
            self.assertEqual(result.returncode, 0)
            data = json.loads(result.stdout)
            src = data['metadata']['sources'][0]
            self.assertIn('original_metadata', src,
                          "merge2 must preserve each source's metadata block "
                          "(legacy taxjson-merge does)")
            self.assertEqual(src['original_metadata']['source_brokerage'], 'ib')


class TestMerge2MissingFiles(unittest.TestCase):
    def test_missing_file_warns_and_skips(self):
        """Stale shell-script with a removed input file must not abort
        the entire run; warn-and-skip preserves the legacy taxjson-merge
        UX so users with long file lists don't lose every other source
        to one missing optional input."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            present = tmp / 'present.json'
            _write_json(present, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0,
            }])
            result = _run_merge2(str(present), str(tmp / 'ghost.json'))
            self.assertEqual(result.returncode, 0)
            self.assertIn('not found, skipping', result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(len(data['transactions']), 1)

    def test_require_inputs_aborts_on_missing_file(self):
        """--require-inputs (passed by the orchestrated pipeline) must fail
        fast on a missing input rather than emit a partial merge that
        silently understates gains. No stdout, nonzero exit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            present = tmp / 'present.json'
            _write_json(present, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0,
            }])
            result = _run_merge2('--require-inputs', str(present),
                                 str(tmp / 'ghost.json'))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), '')
            self.assertIn('refusing to emit a partial merge', result.stderr)

    def test_require_inputs_aborts_on_unreadable_json(self):
        """A corrupt/unreadable EXISTING input under --require-inputs is the
        dangerous case: it would otherwise be skipped, dropping real
        transactions. Must abort."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            good = tmp / 'good.json'
            _write_json(good, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0,
            }])
            corrupt = tmp / 'corrupt.json'
            corrupt.write_text('{ this is not valid json', encoding='utf-8')
            result = _run_merge2('--require-inputs', str(good), str(corrupt))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), '')
            self.assertIn('refusing to emit a partial merge', result.stderr)


if __name__ == '__main__':
    unittest.main()


class TestPostConversionInvariant(unittest.TestCase):
    def _run_main(self, argv, leave_native):
        # A residual native row can only arise from a field-conversion
        # exception (missing rates fall back to --default-rate), so
        # inject one by patching the converter to leave EUR untouched.
        import io
        import json
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from pathlib import Path
        import taxjson.bin.taxjson_merge2 as m2

        def fake_convert(txs, target, history, default_rate):
            for t in txs:
                if not leave_native:
                    t.currency = target
            return txs
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "in.json"
            f.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-01-10",
                 "date_settle": "2026-01-10", "time": "09:30:00",
                 "symbol": "AAA.DE", "quantity": 10, "price": 10.0,
                 "net_amount": 100.0, "currency": "EUR",
                 "account": "m"}]}))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(m2, "convert_transactions",
                                   fake_convert), \
                    mock.patch.object(sys, "argv",
                                      ["taxjson-merge2", *argv,
                                       str(f)]), \
                    redirect_stdout(out), redirect_stderr(err):
                rc = m2.main()
        return rc, out.getvalue(), err.getvalue()

    def test_residual_native_row_fails_validate(self):
        rc, out, err = self._run_main(["--validate", "--to", "CAD"],
                                      leave_native=True)
        self.assertEqual(rc, 1,
                         "residual native row must FAIL --validate")
        self.assertIn("EUR", err)
        self.assertIn("still carry", err)

    def test_residual_native_row_warns_without_validate(self):
        rc, out, err = self._run_main(["--to", "CAD"],
                                      leave_native=True)
        self.assertFalse(rc)
        self.assertIn("still carry", err)
        self.assertIn('"transactions"', out)

    def test_clean_conversion_stays_quiet(self):
        rc, out, err = self._run_main(["--validate", "--to", "CAD"],
                                      leave_native=False)
        self.assertFalse(rc)
        self.assertNotIn("still carry", err)

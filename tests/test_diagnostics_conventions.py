"""Diagnostics conventions (AUDIT-2026-07-ui §1 axis C).

Pins the normalized CLI diagnostics contract:

  C1 — prefixes: GNU style on stderr, `<prog>: warning|error|note: ...`,
       lowercase severity, <prog> = installed console-script name.
  C3 — streams: reports/data on stdout; diagnostics on stderr; "no data"
       is a stdout report line with exit 0.
  C4 — exit codes: 0 = success (incl. no-data); 1 = the tool's FINDING
       (mismatch/violation/lint problem); 2 = usage/environment error.

These are contract tests: if you change a diagnostic's shape, stream, or
exit code, you are changing the CLI contract — update the audit doc too.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(module, *args):
    """Run a bin module via `python -m` (no console-script dependency)."""
    cmd = [sys.executable, '-m', f'taxjson.bin.{module}', *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=os.environ.copy())


def _write_json(path: Path, transactions):
    path.write_text(json.dumps({'transactions': transactions}),
                    encoding='utf-8')


class TestCliDiagHelper(unittest.TestCase):
    def test_gnu_shapes(self):
        from taxjson.lib import cli_diag
        for fn, sev in ((cli_diag.warn, 'warning'),
                        (cli_diag.error, 'error'),
                        (cli_diag.note, 'note')):
            buf = StringIO()
            with redirect_stderr(buf):
                fn('taxjson-x', 'msg here')
            self.assertEqual(buf.getvalue(), f'taxjson-x: {sev}: msg here\n')


class TestMergeDiagnostics(unittest.TestCase):
    def test_missing_file_warns_with_prefix_and_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            present = tmp / 'present.json'
            _write_json(present, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
                'price': 150.0, 'net_amount': 15000.0,
            }])
            r = _run('taxjson_merge', str(present), str(tmp / 'ghost.json'))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('taxjson-merge: warning: ', r.stderr)
            self.assertIn('not found, skipping', r.stderr)
            self.assertNotIn('Warning:', r.stderr)
            # Report (the merged JSON) still lands on stdout.
            self.assertEqual(len(json.loads(r.stdout)['transactions']), 1)


class TestValidateDiagnostics(unittest.TestCase):
    def test_finding_rows_on_stdout_verdict_on_stderr_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad.json'
            _write_json(bad, [{'symbol': 'AAPL.US'}])  # no action/date/currency
            r = _run('taxjson_validate', str(bad))
            self.assertEqual(r.returncode, 1)
            # Per-record detail IS the report: stdout, `ERROR:` row labels.
            self.assertIn('  ERROR: ', r.stdout)
            self.assertNotIn('[ERROR]', r.stdout)
            # Verdict is a C1-prefixed stderr diagnostic (was `FAIL:`).
            self.assertIn('taxjson-validate: error: validation failed', r.stderr)
            self.assertNotIn('FAIL:', r.stdout + r.stderr)

    def test_warning_rows_use_report_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'warn.json'
            _write_json(f, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 1, 'currency': 'USDX',
                'price': 1.0,
            }])
            r = _run('taxjson_validate', '--warnings', str(f))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('  WARNING: ', r.stdout)
            self.assertNotIn('[WARN]', r.stdout)

    def test_load_failure_is_prefixed_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / 'junk.json'
            junk.write_text('not json', encoding='utf-8')
            r = _run('taxjson_validate', str(junk))
            self.assertEqual(r.returncode, 1)
            self.assertIn('taxjson-validate: error: failed to load JSON',
                          r.stderr)


class TestExplainNoMatch(unittest.TestCase):
    def test_no_matching_gains_exits_0_with_note(self):
        """A filter that matches nothing is "no data", not a failure —
        exit 0 with a stderr note (was: exit 1)."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'buys_only.json'
            _write_json(f, [{
                'action': 'BUYSELL', 'date': '2025-01-15',
                'symbol': 'AAPL.US', 'quantity': 10, 'currency': 'USD',
                'price': 100.0, 'net_amount': -1000.0,
            }])
            r = _run('taxjson_explain', '--country', 'canada', str(f))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('taxjson-explain: note: no matching gains found',
                          r.stderr)
            self.assertEqual(r.stdout, '')


class TestBrokerageExitCodes(unittest.TestCase):
    def _main_exit(self, argv, **patches):
        from taxjson.bin import taxjson_brokerage
        err = StringIO()
        ctxs = [patch.object(taxjson_brokerage, k, v)
                for k, v in patches.items()]
        with patch.object(sys, 'argv', argv), redirect_stderr(err):
            for c in ctxs:
                c.start()
            try:
                with self.assertRaises(SystemExit) as cm:
                    taxjson_brokerage.main()
            finally:
                for c in ctxs:
                    c.stop()
        return cm.exception.code, err.getvalue()

    def test_missing_input_file_exits_2(self):
        code, err = self._main_exit(
            ['taxjson-brokerage', '--brokerage', 'kraken', '/no/such/file.csv'])
        self.assertEqual(code, 2)
        self.assertIn('taxjson-brokerage: error: no such file', err)

    def test_strict_schema_violation_is_a_finding_exit_1(self):
        """Schema violations found in the data are FINDINGS → exit 1
        (was exit 2, which the convention reserves for usage/environment)."""
        class _Empty:
            _rows_seen = None

            def parse_file(self, path):
                return []

        with tempfile.NamedTemporaryFile(suffix='.csv') as f:
            code, err = self._main_exit(
                ['taxjson-brokerage', '--brokerage', 'kraken', '--strict',
                 f.name],
                load_brokerage=lambda _id: _Empty,
                validate_transactions=lambda txs, lint=False:
                    (['synthetic violation'], []),
            )
        self.assertEqual(code, 1)
        self.assertIn('synthetic violation', err)

    def test_lint_problems_exit_1_not_3(self):
        class _Unaccounted:
            def __init__(self):
                self._rows_seen = 5
                self._rows_consumed = 1
                self._skip_counts = {}

            def parse_file(self, path):
                return []

        with tempfile.NamedTemporaryFile(suffix='.csv') as f:
            code, err = self._main_exit(
                ['taxjson-brokerage', '--brokerage', 'kraken', '--lint',
                 f.name],
                load_brokerage=lambda _id: _Unaccounted,
                validate_transactions=lambda txs, lint=False: ([], []),
            )
        self.assertEqual(code, 1)
        self.assertIn('unaccounted=4', err)


class TestCorpActionsExitCodes(unittest.TestCase):
    def test_missing_input_csv_exits_2_with_prefix(self):
        r = _run('taxjson_corp_actions', '--brokerage', 'ib',
                 '/no/such/events.csv')
        self.assertEqual(r.returncode, 2)
        self.assertIn('taxjson-corp-actions: error: input CSV not found',
                      r.stderr)


class TestXlsxToCsvExitCodes(unittest.TestCase):
    def test_missing_input_exits_2_with_prefix(self):
        r = _run('xlsx_to_csv', '/no/such/book.xlsx')
        self.assertEqual(r.returncode, 2)
        self.assertIn('taxjson-xlsx-to-csv: error: no such file', r.stderr)
        self.assertNotIn('Error:', r.stderr)



class TestCollectDiagnosticsMarkers(unittest.TestCase):
    def test_accepts_bare_and_prog_prefixed_markers(self):
        """run.py's DIAGNOSTICS collector must keep both the parser-layer
        bare `warning:` shape and the C1 `<prog>: warning:` shape, or the
        prefixed bin-CLI diagnostics silently vanish from .sum files."""
        from taxjson.bin.taxjson_run import collect_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / 'acct_base.diag').write_text(
                'warning: bare parser warning\n'
                '  continuation of the warning\n'
                'taxjson-fill-crypto: warning: failed to fetch crypto price\n'
                'taxjson-validate: error: validation failed with 2 total errors\n'
                'note: a bare note\n'
                'OK: base.json: 12 transactions validated.\n'
                'plain report chatter that must be dropped\n',
                encoding='utf-8')
            kept = collect_diagnostics(cache, 'acct')
            self.assertIn('warning: bare parser warning', kept)
            self.assertIn('  continuation of the warning', kept)
            self.assertIn('taxjson-fill-crypto: warning: failed to fetch',
                          kept)
            self.assertIn('taxjson-validate: error: validation failed', kept)
            self.assertIn('note: a bare note', kept)
            self.assertIn('OK: base.json', kept)
            self.assertNotIn('plain report chatter', kept)


if __name__ == '__main__':
    unittest.main()

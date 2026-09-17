"""Regression tests for `taxjson_run.needs_rebuild`.

The wrapper caches every intermediate stage by mtime to skip work on
re-runs. The cache is dangerous if it can return STALE output —
filing the wrong tax number is worse than re-running for 30 seconds.

These tests pin the three implicit dependencies that make the cache
safe-by-default:

  1. Declared inputs — passed as positional args.
  2. `taxjson.toml` — via the module-level `_CONFIG_PATH`.
  3. The `taxjson` package's own source — via `_package_mtime()`.

Without (3), a code fix would silently reuse pre-fix output until
the user remembered to pass `--force`. That was the failure mode
that hid the cycle-4 Webull `_find_header` regression.
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_run
from taxjson.bin.taxjson_run import needs_rebuild, _package_mtime


class TestNeedsRebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        # Reset module-level state so tests don't pollute each other.
        taxjson_run._CONFIG_PATH = None
        taxjson_run._PACKAGE_MTIME_CACHED = None

    def tearDown(self):
        self.tmp.cleanup()
        taxjson_run._CONFIG_PATH = None
        taxjson_run._PACKAGE_MTIME_CACHED = None

    def _touch(self, name: str, mtime: float = None) -> Path:
        p = self.tmpdir / name
        p.write_text('')
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def test_missing_output_triggers_rebuild(self):
        out = self.tmpdir / 'nope.json'
        self.assertTrue(needs_rebuild(out))

    def test_newer_input_triggers_rebuild(self):
        out = self._touch('out.json', mtime=1000.0)
        inp = self._touch('in.csv', mtime=2000.0)
        self.assertTrue(needs_rebuild(out, inp))

    def test_older_input_does_not_trigger(self):
        # Bake the package-mtime cache to something very old so it
        # doesn't trigger this test on its own.
        taxjson_run._PACKAGE_MTIME_CACHED = 100.0
        out = self._touch('out.json', mtime=3000.0)
        inp = self._touch('in.csv', mtime=2000.0)
        self.assertFalse(needs_rebuild(out, inp))

    def test_newer_taxjson_toml_triggers_rebuild(self):
        taxjson_run._PACKAGE_MTIME_CACHED = 100.0
        cfg = self._touch('taxjson.toml', mtime=5000.0)
        taxjson_run._CONFIG_PATH = cfg
        out = self._touch('out.json', mtime=3000.0)
        self.assertTrue(needs_rebuild(out))

    def test_newer_package_source_triggers_rebuild(self):
        # Simulate "a code change was made AFTER the cached output was
        # written" — the new mechanism must rebuild.
        out = self._touch('out.json', mtime=1000.0)
        taxjson_run._PACKAGE_MTIME_CACHED = 5000.0
        self.assertTrue(needs_rebuild(out))

    def test_older_package_source_does_not_trigger(self):
        # Cache state where package hasn't moved since the output was
        # written. Output should be reused.
        out = self._touch('out.json', mtime=10000.0)
        taxjson_run._PACKAGE_MTIME_CACHED = 100.0
        self.assertFalse(needs_rebuild(out))


class TestPackageMtime(unittest.TestCase):
    def test_returns_recent_positive_float(self):
        taxjson_run._PACKAGE_MTIME_CACHED = None
        m = _package_mtime()
        self.assertIsInstance(m, float)
        self.assertGreater(m, 0.0)


class TestEchoParseStats(unittest.TestCase):
    """`echo_parse_stats` surfaces per-file transaction counts and the
    0-tx warning from the parser's stderr-captured-to-diag into the
    wrapper's console output. Without this, a parser regression that
    silently drops every row in a CSV (the Webull `_find_header`
    failure mode) would only manifest as an empty cache file."""

    def _setup_diag(self, tmpdir: Path, content: str):
        out = tmpdir / "test.json"
        out.write_text("{}")
        diag = out.with_name(out.name + ".diag")
        diag.write_text(content)
        return out

    def test_emits_per_file_counts(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            out = self._setup_diag(Path(tmp),
                "  margin_2025.csv: 142 tax objects\n"
                "  margin_2026.csv: 28 tax objects\n"
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                taxjson_run.echo_parse_stats(out)
            lines = buf.getvalue().splitlines()
            self.assertIn("  margin_2025.csv: 142 tax objects", lines)
            self.assertIn("  margin_2026.csv: 28 tax objects", lines)

    def test_emits_zero_count_warning(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            out = self._setup_diag(Path(tmp),
                "  wb_2024.csv: 0 tax objects\n"
                "warning: wb_2024.csv parsed to 0 transactions (5000 bytes input, brokerage=webull).\n"
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                taxjson_run.echo_parse_stats(out)
            output = buf.getvalue()
            self.assertIn("wb_2024.csv: 0 tax objects", output)
            self.assertIn("warning:", output)
            self.assertIn("parsed to 0 transactions", output)

    def test_ignores_unrelated_stderr_lines(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            out = self._setup_diag(Path(tmp),
                "  some.csv: 5 tax objects\n"
                "NOTE: some diagnostic the .sum file collects\n"
                "OK: dividend reconcile passed\n"
                "error: something else\n"
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                taxjson_run.echo_parse_stats(out)
            lines = buf.getvalue().splitlines()
            # Only the count line. The NOTE/OK/error are routed
            # elsewhere (collect_diagnostics → .sum) and shouldn't
            # double-print here.
            self.assertEqual(lines, ["  some.csv: 5 tax objects"])

    def test_no_diag_no_output(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "no_diag.json"
            out.write_text("{}")
            buf = io.StringIO()
            with redirect_stdout(buf):
                taxjson_run.echo_parse_stats(out)
            self.assertEqual(buf.getvalue(), "")


if __name__ == '__main__':
    unittest.main()

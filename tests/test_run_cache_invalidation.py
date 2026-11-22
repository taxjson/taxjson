"""Regression tests for `pipeline/freshness.needs_rebuild`.

The wrapper caches every intermediate stage by content (a record per output
of what it was built from; PLAN step 3a) to skip work on re-runs. The cache is dangerous if it can return STALE output —
filing the wrong tax number is worse than re-running for 30 seconds.

These tests pin the three implicit dependencies that make the cache
safe-by-default:

  1. Declared inputs — passed as positional args.
  2. `taxjson.toml` — via `run_state.config_path`.
  3. The `taxjson` package's own source — via `package_fingerprint()`.

Without (3), a code fix would silently reuse pre-fix output until
the user remembered to pass `--force`. That was the failure mode
that hid the cycle-4 Webull `_find_header` regression.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.pipeline import diagnostics as pipeline_diagnostics
from taxjson.pipeline.freshness import (
    commit_records, mark_built, needs_rebuild, package_fingerprint,
    record_now, unmark)
from taxjson.pipeline import run_state
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestNeedsRebuild(unittest.TestCase):
    """The content rule (PLAN step 3a): an output is fresh when a complete
    run recorded what it was built from and nothing of that changed."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        run_state.config_path = None
        run_state.package_fingerprint = "code-A"
        run_state.current.pending_records.clear()

    def tearDown(self):
        self.tmp.cleanup()
        run_state.config_path = None
        run_state.package_fingerprint = None
        run_state.current.pending_records.clear()

    def _file(self, name: str, text: str = "", mtime: float = None) -> Path:
        p = self.tmpdir / name
        p.write_text(text)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p

    def _built(self, out: Path, *inputs: Path) -> None:
        """What a stage does: asks, builds, and the run vouches for it."""
        self.assertTrue(needs_rebuild(out, *inputs))
        out.write_text(out.read_text() if out.exists() else "built")
        commit_records()

    def test_missing_output_triggers_rebuild(self):
        self.assertTrue(needs_rebuild(self.tmpdir / "nope.json"))

    def test_an_output_no_run_vouched_for_is_rebuilt(self):
        out = self._file("out.json", "x", mtime=10000.0)
        inp = self._file("in.csv", "a", mtime=1000.0)
        self.assertTrue(needs_rebuild(out, inp))        # no record: not vouched for

    def test_recorded_and_unchanged_is_fresh_whatever_the_mtimes(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        os.utime(inp, (9e9, 9e9))                        # touched, same bytes
        os.utime(out, (1.0, 1.0))                        # restored with an old mtime
        self.assertFalse(needs_rebuild(out, inp))

    def test_a_changed_or_deleted_input_triggers_rebuild(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        st = inp.stat()
        inp.write_text("b")
        os.utime(inp, ns=(st.st_atime_ns, st.st_mtime_ns))   # cp -p: same mtime
        run_state.current.hash_cache.clear()            # the next run is another process
        self.assertTrue(needs_rebuild(out, inp))
        self._built(out, inp)
        inp.unlink()                                     # the deletion-blindness class
        self.assertTrue(needs_rebuild(out, inp))

    def test_an_input_that_appears_triggers_rebuild(self):
        inp = self.tmpdir / "later.csv"
        out = self._file("out.json", "built")
        self._built(out, inp)                            # recorded as absent
        inp.write_text("a")
        self.assertTrue(needs_rebuild(out, inp))

    def test_taxjson_toml_and_the_code_are_implicit_inputs(self):
        cfg = self._file("taxjson.toml", "year = 2025\n")
        run_state.config_path = cfg
        out = self._file("out.json", "built")
        self._built(out)
        self.assertFalse(needs_rebuild(out))
        cfg.write_text("year = 2026\n")
        self.assertTrue(needs_rebuild(out))
        self._built(out)
        run_state.package_fingerprint = "code-B"        # an upgrade
        self.assertTrue(needs_rebuild(out))

    def test_an_output_rewritten_behind_the_record_is_rebuilt(self):
        out = self._file("out.json", "built")
        self._built(out)
        out.write_text("someone else's")
        self.assertTrue(needs_rebuild(out))

    def test_an_input_no_longer_declared_triggers_rebuild(self):
        # A map removed, the missing history withdrawn: the stage no longer
        # declares it, and that alone is a change (no marker, no bump:
        # TJ-1807).
        inp = self._file("in.csv", "a")
        stamp = self._file("missing_history.state", "x")
        out = self._file("out.json", "built")
        self._built(out, inp, stamp)
        self.assertFalse(needs_rebuild(out, inp, stamp))
        self._built(out, inp)
        self.assertFalse(needs_rebuild(out, inp))

    def test_force_rebuilds_and_still_records(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        self.assertTrue(needs_rebuild(out, inp, force=True))
        commit_records()
        self.assertFalse(needs_rebuild(out, inp))

    def test_a_run_that_stops_vouches_for_nothing(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self.assertTrue(needs_rebuild(out, inp))
        run_state.current.pending_records.clear()       # the run died before commit
        self.assertTrue(needs_rebuild(out, inp))

    def test_the_inputs_are_read_when_the_stage_asks(self):
        """A file saved while a later stage ran is a change the next --fast
        sees: the record holds the input as it was built from."""
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self.assertTrue(needs_rebuild(out, inp))
        inp.write_text("b")                              # saved before the commit
        commit_records()
        self.assertTrue(needs_rebuild(out, inp))

    def test_a_moved_project_keeps_its_records(self):
        proj = self.tmpdir / "proj"
        (proj / "work").mkdir(parents=True)
        inp = proj / "inputs.csv"
        inp.write_text("a")
        out = proj / "work" / "out.json"
        out.write_text("built")
        self._built(out, inp)
        moved = self.tmpdir / "proj2"
        proj.rename(moved)
        self.assertFalse(needs_rebuild(moved / "work" / "out.json", moved / "inputs.csv"))
        rec = json.loads((moved / "work" / ".deps" / "out.json.json").read_text())
        self.assertEqual(list(rec["inputs"]), ["inputs.csv"])     # relative, no home path

    def test_a_build_decided_elsewhere_is_recorded_with_mark_built(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        out.write_text("rebuilt on the stage's own ground")
        mark_built(out, inp)
        commit_records()
        self.assertFalse(needs_rebuild(out, inp))
        self.assertTrue(needs_rebuild(out, inp, force=True))
        unmark(out)                                      # kept the old file after all
        commit_records()
        self.assertFalse(needs_rebuild(out, inp))        # the old record still holds

    def test_a_record_that_cannot_be_written_is_reported_not_raised(self):
        out = self._file("out.json", "built")
        (self.tmpdir / ".deps").write_text("in the way")
        self.assertTrue(needs_rebuild(out))
        self.assertEqual(commit_records(), [out])
        self.assertTrue(needs_rebuild(out))

    def test_taxjson_toml_is_read_when_the_stage_asks_too(self):
        cfg = self._file("taxjson.toml", "year = 2025\n")
        run_state.config_path = cfg
        out = self._file("out.json", "built")
        self.assertTrue(needs_rebuild(out))
        cfg.write_text("year = 2026\n")                   # saved during a later stage
        commit_records()
        self.assertTrue(needs_rebuild(out))

    def test_an_older_taxjson_s_record_is_none(self):
        # Format 1 (before TJ-4266): its output is built again, so the
        # first run after the upgrade is a full rebuild.
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        rp = self.tmpdir / ".deps" / "out.json.json"
        rec = json.loads(rp.read_text())
        self.assertEqual(rec["format"], 2)
        rp.write_text(json.dumps({**rec, "format": 1}))
        self.assertTrue(needs_rebuild(out, inp))

    def test_another_module_s_field_is_kept_when_the_run_vouches_again(self):
        # A wash pass's own record (lib/report_model `wash`) is its wash
        # file's record's (TJ-1804): a rebuild keeps it.
        from taxjson.lib.records import read_record, set_record_field
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self._built(out, inp)
        set_record_field(out, "wash", {"blend": "blend"})
        out.write_text("rebuilt")
        self._built(out, inp)
        self.assertEqual(read_record(out)["wash"], {"blend": "blend"})
        self.assertFalse(needs_rebuild(out, inp))

    def test_record_now_vouches_for_one_output(self):
        inp = self._file("in.csv", "a")
        out = self._file("out.json", "built")
        self.assertTrue(needs_rebuild(out, inp))
        record_now(out)
        self.assertFalse(needs_rebuild(out, inp))
        self.assertEqual(run_state.current.pending_records, {})


class TestPackageFingerprint(unittest.TestCase):
    def test_once_per_process(self):
        run_state.package_fingerprint = None
        a = package_fingerprint()
        self.assertEqual(len(a), 64)
        self.assertIs(package_fingerprint(), a)
        run_state.package_fingerprint = None


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
                pipeline_diagnostics.echo_parse_stats(out)
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
                pipeline_diagnostics.echo_parse_stats(out)
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
                pipeline_diagnostics.echo_parse_stats(out)
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
                pipeline_diagnostics.echo_parse_stats(out)
            self.assertEqual(buf.getvalue(), "")


if __name__ == '__main__':
    unittest.main()

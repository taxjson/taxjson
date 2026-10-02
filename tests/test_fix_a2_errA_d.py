"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, the web helpers, export,
some parsers and taxjson-sum-income."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def tj(root, *args):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=ENV, stdin=subprocess.DEVNULL)


def tool(module, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-c",
                           "import sys; from taxjson.bin._entry import "
                           f"{module} as m; sys.exit(m())", *args],
                          capture_output=True, text=True, cwd=cwd,
                          env=env or ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


# ------------------------------------------------------------- checklist
class TestChecklistState(_Tmp):
    """A2-0768, A2-0789, A2-1393, A2-1414, A2-1430 (checklist half)."""

    def project(self):
        (self.root / "taxjson.toml").write_text(TOML)
        return self.root

    def test_state_is_directory_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_state_symlink_loop_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        os.symlink(cl.STATE_FILE, root / cl.STATE_FILE)
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_wrong_shape_override_entry_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        for bad in ('"x"', "7", "[1]", '{"status": 5}'):
            (root / cl.STATE_FILE).write_text(
                '{"overrides": {"elections": %s}}' % bad)
            with self.assertRaises(cl.StateFileError) as cm:
                cl.load_state(root)
            self.assertIn("elections", str(cm.exception))

    def test_bom_state_loads(self):
        """A2-0776: a hand-edited checklist.json saved with a BOM."""
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).write_bytes(
            b'\xef\xbb\xbf{"overrides": {"elections": {"status": "done"}}}')
        self.assertEqual(cl.load_state(root)["overrides"]["elections"]
                         ["status"], "done")

    def test_done_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--done", "elections")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    def test_reset_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--reset")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_done_read_only_file_one_line_and_kept(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        root.chmod(0o555)
        try:
            r = tj(root, "checklist", "--done", "elections")
        finally:
            root.chmod(0o755)
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot write", r.stderr)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])

    def test_save_failure_leaves_old_file_and_no_part(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        state = cl.load_state(root)
        state["bad"] = object()          # json.dumps fails part-way
        with self.assertRaises((cl.StateFileError, TypeError)):
            cl.save_state(root, state, 2025)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])


class TestWatchStateInner(unittest.TestCase):
    """A2-1430 (watch half): wrong-shape inner entries re-baseline."""

    def test_bad_inner_rebaselines(self):
        from taxjson.bin import taxjson_watch as w
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            for radar, hn in (([1], None), ({"SHOP.TO": "x"}, None),
                              ({"SHOP.TO": {"category": 5}}, None),
                              ({}, "abc"), ({}, True)):
                doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                       "radar": radar}
                if hn is not None:
                    doc["harvest_now"] = hn
                p.write_text(json.dumps(doc))
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertIsNone(w.load_state(p), (radar, hn))
                self.assertIn("NEW baseline", err.getvalue())

    def test_good_state_kept(self):
        from taxjson.bin import taxjson_watch as w
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                   "radar": {"SHOP.TO": {"category": "LOCKED",
                                         "advisory": "a",
                                         "clears_at": "2025-01-01"}},
                   "harvest_now": -12.5}
            p.write_text(json.dumps(doc))
            self.assertEqual(w.load_state(p)["harvest_now"], -12.5)


if __name__ == "__main__":
    unittest.main()

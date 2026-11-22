"""Backlog group `errors` (2026-10-25): error messages, atomic writes and
the smaller commands (elect, fetch --list, slips, t1135, tax-logic).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.

Formerly: test_fix_backlog_errors
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


SPIN = "20240603-parnt-spnco-3509"      # the demo's spin-off (synthetic)


def _demo(tmp):
    """`init --demo` (the years layout: exports shared in inputs/, a
    saved spin-off election); the 2024 year folder."""
    r = _tjs(None, "init", "--demo", str(Path(tmp) / "demo"))
    assert r.returncode == 0, r.stderr
    return Path(tmp) / "demo" / "2024"


class TestElect(unittest.TestCase):
    """TJ-0037 and TJ-0176: `tjs elect` in the years layout.

    Formerly: test_fix_backlog_errors.TestElect"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.year = _demo(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_missing_hint_is_explained_in_place(self):
        # TJ-0037: an event already elected is no pending election, so
        # `elect --pending` (the old pointer) said "No pending elections".
        r = _tjs(self.year, "elect", "margin", "--set",
                 f"{SPIN}=rollover_s_86_1")
        # A usage error: exit 2 (backlog TJ-0539).
        self.assertEqual(r.returncode, 2, r.stderr)
        text = " ".join(r.stderr.split())
        self.assertIn("rollover_s_86_1 needs allocated_acb_cad", text)
        self.assertIn("allocated_acb_cad: Cost (in CAD) moved from the "
                      "parent to the spun-off shares", text)
        self.assertIn("Add `--hint allocated_acb_cad=<value>`. Nothing was "
                      "saved.", text)
        self.assertNotIn("--pending", text)
        r = _tjs(self.year, "elect", "--pending")
        self.assertIn("No pending elections", r.stdout + r.stderr)

    def test_shared_manifest_is_shown_relative(self):
        # TJ-0176: the shared manifest is ../inputs/..., not the
        # absolute path (153 columns in a temp folder).
        r = _tjs(self.year, "elect")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("margin — ../inputs/margin/manifest.json", r.stdout)
        self.assertNotIn(str(self.year.parent), r.stdout)

    def test_set_and_reset_name_the_manifest_relative(self):
        with tempfile.TemporaryDirectory() as d:
            year = _demo(d)
            r = _tjs(year, "elect", "margin", "--reset", "--event", SPIN)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Cleared 1 election(s) from "
                          "../inputs/margin/manifest.json.", r.stdout)
            r = _tjs(year, "elect", "margin", "--set",
                     f"{SPIN}=rollover_s_86_1", "--hint",
                     "allocated_acb_cad=100")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("../inputs/margin/manifest.json", r.stdout)
            self.assertNotIn(str(year.parent), r.stdout)


if __name__ == "__main__":
    unittest.main()

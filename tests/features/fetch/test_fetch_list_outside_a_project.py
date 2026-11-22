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


class TestFetchListAnywhere(unittest.TestCase):
    """TJ-0081: `fetch --list` reads no project, so it runs in any
    folder (the installer runs it from the user's own folder); `fetch`
    itself still needs one.

    Formerly: test_fix_backlog_errors.TestFetchListAnywhere"""

    def _listed(self, r):
        if r.returncode == 2:
            # No fetcher plugin installed here: the one install hint.
            self.assertIn("no fetcher is installed", r.stderr)
        else:
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("not a taxjson project", r.stderr)

    def test_list_in_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self._listed(_tjs(None, "fetch", "--list", cwd=d))
            self._listed(_tjs(d, "fetch", "--list", "--json"))
            r = _tjs(None, "fetch", cwd=d)
            if "no fetcher is installed" in r.stderr:
                # No plugin installed (CI's matrix jobs install it after
                # the suite; a fetcher is found by its entry point, not
                # the PYTHONPATH): that refusal comes first.
                self.assertEqual(r.returncode, 2, r.stderr)
            else:
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("not a taxjson project", r.stderr)

    def test_list_in_the_years_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "2024").mkdir()
            (Path(d) / "2024" / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n')
            self._listed(_tjs(d, "fetch", "--list"))


if __name__ == "__main__":
    unittest.main()

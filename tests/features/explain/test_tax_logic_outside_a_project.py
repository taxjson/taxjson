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


class TestTaxLogicOutsideAProject(unittest.TestCase):
    """TJ-0187: outside a project the title says "default settings",
    never "with this project's settings".

    Formerly: test_fix_backlog_errors.TestTaxLogicOutsideAProject"""

    def test_title(self):
        from taxjson.lib import tax_logic as TL
        for c in ("canada", "usa"):
            head = TL.render(c, {}).splitlines()[0]
            self.assertTrue(head.endswith(", default settings"), head)
            self.assertNotIn("project", head)
        head = TL.render("canada", {"year": 2024, "country": "canada"}
                         ).splitlines()[0]
        self.assertEqual(
            head, "TAX LOGIC — Canada, tax year 2024, with this project's "
                  "settings")

    def test_cli_in_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as d:
            r = _tjs(d, "tax-logic", "--country", "canada", "--ids")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.splitlines()[0],
                             "TAX LOGIC — Canada, default settings")


if __name__ == "__main__":
    unittest.main()

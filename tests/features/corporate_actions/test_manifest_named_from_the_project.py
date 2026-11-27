"""Backlog fix round 2026-10-29, the `messages` group.

Synthetic data only (made-up coins ZZC / ZZD, accounts 999000xx).

Formerly: test_fix_1029_messages
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


# ------------------------------------------------------------- TJ-0414

def _tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


# ------------------------------------------------------------- TJ-0216

class TestCorpActionsNamesTheManifestShort(unittest.TestCase):
    """TJ-0216: the corp-actions stage named the elections manifest by
    its absolute path (captured in the .diag); the console turned it
    into inputs/margin/elections.json in a year folder whose exports are
    in ../inputs/. It is named as `tjs elections` names it.

    Formerly: test_fix_1029_messages.TestCorpActionsNamesTheManifestShort"""

    def test_pending_election_names_the_shared_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "demo"
            r = _tjs("init", "--demo", str(demo))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (demo / "inputs" / "margin" / "elections.json").write_text(
                '{"elections": {}}\n')
            r = _tjs("-C", str(demo / "2024"), "run", "--account",
                     "margin", "--no-input")
            diag = "".join(
                p.read_text(encoding="utf-8") for p in
                (demo / "2024" / "work").glob("margin_*corp.json.diag"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("are saved in ../inputs/margin/elections.json.",
                      r.stdout + r.stderr)
        self.assertIn("the elections are saved in "
                      "../inputs/margin/elections.json.", diag)
        self.assertNotIn(f"{td}/demo/inputs/margin/elections.json",
                         diag + r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

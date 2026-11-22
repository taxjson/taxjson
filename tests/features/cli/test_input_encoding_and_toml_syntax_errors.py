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


class TestEncodingAndSyntaxErrors(unittest.TestCase):
    """TJ-0123: an input's encoding or TOML syntax error names the file
    the way the run names inputs (never the absolute path), the headline
    on the label's line, the fix a detail; a hand-typed .tt is not told
    to re-export it.

    Formerly: test_fix_backlog_errors.TestEncodingAndSyntaxErrors"""

    def _group(self, name, data):
        from taxjson.pipeline.inputs import group_inputs_detailed
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / name).write_bytes(data)
            with self.assertRaises(SystemExit) as cm:
                group_inputs_detailed(acct)
            msg = str(cm.exception)
            self.assertNotIn(td, msg)
        return msg

    def test_latin1_csv(self):
        msg = self._group("zz_export.csv",
                          "Date,Description\n2024-01-02,SOCIÉTÉ\n"
                          .encode("latin-1"))
        lines = msg.splitlines()
        self.assertIn("inputs/margin/zz_export.csv: not UTF-8 text (byte "
                      "0xc9 at offset ", lines[0])
        self.assertEqual(lines[1].strip(),
                         "Re-export it, or save it as UTF-8 in your editor.")

    def test_empty_csv(self):
        lines = self._group("zz_empty.csv", b"").splitlines()
        self.assertIn("inputs/margin/zz_empty.csv is empty — a failed or "
                      "interrupted download?", lines[0])
        self.assertEqual(lines[1].strip(),
                         "Download the export again (or remove the file).")

    def test_toml_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2024 =\n')
            r = _tjs(None, "fees", cwd=td)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn(td, r.stderr)
            self.assertIn("error: taxjson.toml:3: not valid TOML: ",
                          r.stderr)
            self.assertIn("(column ", r.stderr)
            self.assertIn("  Correct that line in taxjson.toml", r.stderr)

    def test_a_typed_file_is_saved_again_not_reexported(self):
        from taxjson.lib.cli_diag import utf8_fix
        self.assertEqual(utf8_fix("inputs/margin/hand.tt"),
                         "save it as UTF-8 in your editor")
        self.assertEqual(utf8_fix("ticker.map"),
                         "save it as UTF-8 in your editor")
        self.assertIn("re-export it", utf8_fix("inputs/margin/x.csv"))

    def test_stage_input_named_as_the_run_names_it(self):
        from taxjson.pipeline.tools import _stage_text_shown
        tt = "/tmp/zzproj/inputs/margin/hand.tt"
        text = (f"taxjson-convert-tt: error: {tt}: not UTF-8 text (byte "
                f"0xff at offset 0) — save it as UTF-8 in your editor\n")
        shown = _stage_text_shown(text, ["python", "-m",
                                         "taxjson.bin.taxjson_convert_tt",
                                         "--account-name", "margin", tt])
        self.assertIn("error: inputs/margin/hand.tt: not UTF-8", shown)
        self.assertNotIn("/tmp/zzproj", shown)

    def test_label_never_alone(self):
        from taxjson.lib import out
        lines = out.message("error", "/tmp/" + "z" * 130 + "/a.csv: not "
                            "UTF-8 text", width_=120)
        self.assertTrue(lines[0].startswith("Error: /tmp/zzz"), lines)


if __name__ == "__main__":
    unittest.main()

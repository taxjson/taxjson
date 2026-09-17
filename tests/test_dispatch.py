"""In-process tool dispatch (lib/dispatch.py).

The orchestrator's `python -m taxjson.bin.X` commands execute
IN-PROCESS (frozen-app prerequisite; faster runs). These tests pin the
contract: capture modes, SystemExit mapping, argv isolation, crash
isolation, cwd handling, and the subprocess escape hatch.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import run_cmd, tool_module


def _tool(name, *args):
    return [sys.executable, "-m", f"taxjson.bin.{name}", *args]


class TestToolModule(unittest.TestCase):
    def test_recognizes_tool_shape(self):
        self.assertEqual(
            tool_module(_tool("taxjson_sort", "a.json")),
            ("taxjson.bin.taxjson_sort", ["a.json"]))

    def test_rejects_other_commands(self):
        self.assertIsNone(tool_module(["ls", "-la"]))
        self.assertIsNone(tool_module([sys.executable, "script.py"]))
        self.assertIsNone(tool_module(
            [sys.executable, "-m", "json.tool"]))


class TestRunCmd(unittest.TestCase):
    def test_capture_output_and_exit_code(self):
        # taxjson-validate on a valid file: rc 0, report on stdout.
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "base.json"
            f.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-01-05",
                 "time": "09:30:00", "symbol": "AAA.TO",
                 "quantity": 100, "price": 10.0, "net_amount": 1000.0,
                 "currency": "CAD", "account": "m"}]}))
            r = run_cmd(_tool("taxjson_validate", str(f)),
                        capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsInstance(r.stdout, str)

    def test_sys_exit_message_goes_to_stderr_rc_1(self):
        # A nonexistent input makes tools sys.exit("message").
        r = run_cmd(_tool("taxjson_validate", "/nonexistent.json"),
                    capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((r.stderr or "").strip())

    def test_argv_isolated(self):
        before = list(sys.argv)
        run_cmd(_tool("taxjson_validate", "/nonexistent.json"),
                capture_output=True)
        self.assertEqual(sys.argv, before)

    def test_stdout_file_mode_writes_file_and_captures_stderr(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "base.json"
            f.write_text(json.dumps({"transactions": []}))
            out_path = Path(td) / "out.txt"
            with out_path.open("w", encoding="utf-8") as out:
                r = run_cmd(_tool("taxjson_validate", str(f)),
                            stdout=out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(r.stdout)          # went to the file
            self.assertIsInstance(r.stderr, str)

    def test_cwd_honored_and_restored(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "base.json"
            f.write_text(json.dumps({"transactions": []}))
            # Relative path only resolves if cwd was applied.
            r = run_cmd(_tool("taxjson_validate", "base.json"),
                        capture_output=True, cwd=td)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.getcwd(), old)

    def test_crash_isolation(self):
        # A tool crash (bad JSON -> unhandled or handled error) must be
        # a nonzero rc with stderr text, never an exception here.
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "bad.json"
            f.write_text("{not json")
            r = run_cmd(_tool("taxjson_validate", str(f)),
                        capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((r.stderr or "").strip())

    def test_env_escape_hatch_forces_subprocess(self):
        os.environ["TAXJSON_DISPATCH"] = "subprocess"
        try:
            r = run_cmd(_tool("taxjson_validate", "/nonexistent.json"),
                        capture_output=True)
        finally:
            del os.environ["TAXJSON_DISPATCH"]
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((r.stderr or "").strip())

    def test_live_mode_returns_none_streams(self):
        # No capture flags: tool streams to current stdio.
        buf_out, buf_err = io.StringIO(), io.StringIO()
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            r = run_cmd(_tool("taxjson_validate", "/nonexistent.json"))
        self.assertIsNone(r.stdout)
        self.assertIsNone(r.stderr)
        self.assertTrue(buf_err.getvalue().strip())   # streamed live


if __name__ == "__main__":
    unittest.main()

"""Re-audit-2 fixes: one-line errors and consistent exit codes
(errors-01 / errors-03 / errors-04 lists, errA area).

Synthetic data only.

Formerly: test_fix_a2_errA
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin._entry import _PROG_ALIASES, console_prog
from taxjson.lib import cli_diag
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tool(mod, *args, env=None, cwd=None):
    """The console script `taxjson-<tool>` (its entry point,
    taxjson.bin._entry:<mod>)."""
    e = dict(os.environ, **(env or {}))
    code = (f"import sys; from taxjson.bin._entry import {mod} as m; "
            f"sys.argv[0] = {console_prog(mod)!r}; sys.exit(m())")
    return subprocess.run([sys.executable, "-c", code, *map(str, args)],
                          capture_output=True, text=True, env=e, cwd=cwd,
                          stdin=subprocess.DEVNULL)


def _gains_doc(rows=None, inv=None):
    return {"transactions": rows if rows is not None else [
        {"symbol": "ZZQ.TO", "date": "2025-03-03", "qty": 10,
         "proceeds": 120.0, "cost": 100.0, "gain": 20.0,
         "currency": "CAD", "account": "margin"}],
        "inventory": inv or []}


def _base_row(**kw):
    r = {"action": "BUYSELL", "date": "2025-01-06", "time": "10:00:00",
         "symbol": "ZZQ.TO", "quantity": 10, "price": 10.0,
         "net_amount": -100.0, "commission": 0.0, "currency": "CAD",
         "account": "margin", "id": "t1"}
    r.update(kw)
    return r


class TestGuardMain(unittest.TestCase):
    """A2-0791 / A2-0770 / A2-1426 / A2-1427: the shared guard.

    Formerly: test_fix_a2_errA.TestGuardMain"""

    def _guarded(self, exc):
        def main():
            raise exc
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            cli_diag.guard_main("taxjson-x")(main)()
        return cm.exception.code, err.getvalue()

    def test_symlink_loop_oserror_is_one_line(self):
        import errno
        rc, err = self._guarded(OSError(errno.ELOOP, "Too many levels of "
                                        "symbolic links", "a.json"))
        self.assertEqual(rc, 2)
        self.assertIn("cannot read a.json", err)

    def test_pathlib_symlink_loop_runtime_error(self):
        rc, err = self._guarded(RuntimeError("Symlink loop from 'a.json'"))
        self.assertEqual(rc, 2)
        self.assertIn("Symlink loop", err)

    def test_other_runtime_error_still_raises(self):
        def main():
            raise RuntimeError("bug")
        with self.assertRaises(RuntimeError):
            cli_diag.guard_main("taxjson-x")(main)()

    def test_input_content_error_is_one_line_exit_2(self):
        rc, err = self._guarded(cli_diag.InputContentError("f.map: bad"))
        self.assertEqual(rc, 2)
        self.assertEqual(err.strip(), "taxjson-x: error: f.map: bad")

    def test_input_file_error_is_an_input_content_error(self):
        from taxjson.lib.json_input import InputFileError
        self.assertTrue(issubclass(InputFileError,
                                   cli_diag.InputContentError))
        self.assertTrue(issubclass(InputFileError, ValueError))

    def test_console_prog_names(self):
        self.assertEqual(console_prog("taxjson.bin.taxjson_corp_"
                                      "actions"),
                         "taxjson-corp-actions")
        self.assertEqual(console_prog("taxjson_fees"),
                         "taxjson-fees-sum")
        self.assertEqual(console_prog("to_base_curr"),
                         "taxjson-to-base-curr")

    def test_console_prog_is_each_scripts_name(self):
        # Every console script routed through the trampoline is named as
        # pyproject.toml names it (the alias table cannot drift).
        from taxjson.project.toml_io import read_toml
        root = _paths.REPO
        scripts = read_toml(root / "pyproject.toml")["project"]["scripts"]
        routed = {k: v.split(":", 1)[1] for k, v in scripts.items()
                  if v.startswith("taxjson.bin._entry:")}
        self.assertGreater(len(routed), 20)
        for prog, mod in routed.items():
            self.assertEqual(console_prog(mod), prog, mod)
        # And no name is spelled out for a script that is gone.
        self.assertLessEqual(set(_PROG_ALIASES), set(routed.values()))

    def test_symlink_loop_input_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            loop = Path(td) / "loop.json"
            loop.symlink_to("loop.json")
            r = _tool("taxjson_gains", "--country", "canada", loop)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cannot read", r.stderr)

    def test_rename_cycle_is_one_line_in_stand_alone_tools(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "ticker.map"
            m.write_text("GLOBAL AAQ.US BBQ.US\nGLOBAL BBQ.US AAQ.US\n")
            b = Path(td) / "b.json"
            b.write_text(json.dumps({"transactions": [_base_row()]}))
            for mod, args in (("taxjson_ticker_map", (b, m)),
                              ("taxjson_merge2", ("--map", m, b))):
                r = _tool(mod, *args)
                self.assertNotIn("Traceback", r.stderr, mod)
                self.assertEqual(r.returncode, 2, (mod, r.stderr))
                self.assertIn("rename cycle", r.stderr)

    def test_closed_pipe_is_a_quiet_exit(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g_gains.json"
            g.write_text(json.dumps(_gains_doc()))
            p = subprocess.Popen([sys.executable, "-c",
                                  "import sys; from taxjson.bin._entry "
                                  "import taxjson_sum_gains as m; "
                                  "sys.exit(m())", str(g)],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 stdin=subprocess.DEVNULL)
            p.stdout.close()
            err = p.stderr.read().decode()
            rc = p.wait()
            p.stderr.close()
        self.assertNotIn("BrokenPipeError", err)
        self.assertNotIn("Traceback", err)
        self.assertIn(rc, (0, cli_diag.BROKEN_PIPE_EXIT))

    def test_ascii_stdout_does_not_crash(self):
        r = _tool("taxjson_gains", "--help",
                  env={"PYTHONIOENCODING": "ascii"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("UnicodeEncodeError", r.stderr)


class TestExitCodes(unittest.TestCase):
    """A2-1421 / A2-1435 / A2-1436: a missing or unreadable named input
    exits 2 (usage/environment), never 1 (a finding) or 0 (ignored).

    Formerly: test_fix_a2_errA.TestExitCodes"""

    def test_brokerage_security_overrides_not_utf8(self):
        with tempfile.TemporaryDirectory() as td:
            o = Path(td) / "ov.txt"
            o.write_bytes(b"caf\xe9 | USD | ZZQ.US\n")
            c = Path(td) / "q.csv"
            c.write_text("x\n")
            r = _tool("taxjson_brokerage", "--brokerage", "questrade",
                      "--security-overrides", o, c)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("not UTF-8", r.stderr)

    def test_wash_radar_missing_incomplete_history(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": [_base_row()]}))
            r = _tool("taxjson_wash_radar", "--country", "canada",
                      "--incomplete-history", Path(td) / "nope.json",
                      "--taxable", b)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("error: --incomplete-history", r.stderr)

    def test_generate_parser_missing_input(self):
        r = _tool("taxjson_generate_parser", "/nonexistent/x.csv")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_redact_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tool("taxjson_redact", "--no-denylist",
                      Path(td) / "nope.csv")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("error:", r.stderr)

    def test_missing_ticker_map_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            nope = td / "nope.map"
            csv_ = td / "ib.csv"
            csv_.write_text("Statement,Header,Field Name,Field Value\n")
            b = td / "margin_base.json"
            b.write_text(json.dumps({"transactions": [_base_row()]}))
            dmap = td / "taxjson.toml"     # no [[distributions]]
            dmap.write_text("")
            g = td / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc()))
            runs = {
                "taxjson_corp_actions": ("--brokerage", "ib", "--country",
                                         "canada", "--list", "--ticker-map",
                                         nope, csv_),
                "taxjson_apply_distributions": ("--config", dmap, "--ticker-map",
                                                nope, b),
                "taxjson_harvest": ("--no-ibkr", "--country", "canada",
                                    "--ticker-map", nope, g),
            }
            for mod, args in runs.items():
                r = _tool(mod, *args, env={"TAXJSON_OFFLINE": "1"})
                self.assertEqual(r.returncode, 2, (mod, r.stderr))
                self.assertIn("--ticker-map", r.stderr, mod)


class TestOutputPaths(unittest.TestCase):
    """A2-1428 / A2-1432: an unwritable output says 'cannot write'.

    Formerly: test_fix_a2_errA.TestOutputPaths"""

    def test_wash_radar_json_out_under_a_file(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": [_base_row()]}))
            f = Path(td) / "afile"
            f.write_text("")
            r = _tool("taxjson_wash_radar", "--country", "canada",
                      "--json-out", f / "x.json", "--taxable", b)
            r2 = _tool("taxjson_wash_radar", "--country", "canada",
                       "--json-out", td, "--taxable", b)
        for res in (r, r2):
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertNotIn("Traceback", res.stderr)
            self.assertIn("cannot write", res.stderr)
            self.assertNotIn("cannot read", res.stderr)

    def test_gains_suggest_phantoms_to_a_directory(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": [
                _base_row(quantity=-10, net_amount=100.0)]}))
            for out in (Path(td), Path(td) / "nodir" / "p.json"):
                r = _tool("taxjson_gains", "--country", "canada",
                          "--suggest-missing-history", out, b)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("cannot write", r.stderr)


if __name__ == "__main__":
    unittest.main()

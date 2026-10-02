"""Re-audit-2 fixes: one-line errors and consistent exit codes
(errors-01 / errors-03 / errors-04 lists, errA area).

Synthetic data only.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from taxjson.lib import cli_diag


def _tool(mod, *args, env=None, cwd=None):
    """The console script `taxjson-<tool>` (its entry point,
    taxjson.bin._entry:<mod>)."""
    e = dict(os.environ, **(env or {}))
    code = (f"import sys; from taxjson.bin._entry import {mod} as m; "
            f"sys.argv[0] = {cli_diag.console_prog(mod)!r}; sys.exit(m())")
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
    """A2-0791 / A2-0770 / A2-1426 / A2-1427: the shared guard."""

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
        self.assertEqual(cli_diag.console_prog("taxjson.bin.taxjson_corp_"
                                               "actions"),
                         "taxjson-corp-actions")
        self.assertEqual(cli_diag.console_prog("taxjson_fees"),
                         "taxjson-fees-sum")
        self.assertEqual(cli_diag.console_prog("to_base_curr"),
                         "taxjson-to-base-curr")

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


class TestDispatchGuard(unittest.TestCase):
    """A2-0161 / A2-1417: `taxjson <cmd>` runs a tool's main under the
    console script's guard, in process and out of process."""

    def _cmd(self, mod, *args):
        return [sys.executable, "-m", f"taxjson.bin.{mod}", *map(str, args)]

    def test_unreadable_input_in_process_is_one_line(self):
        from taxjson.lib.dispatch import run_cmd
        mod = types.ModuleType("taxjson.bin._zz_errA_unreadable")

        def main():
            raise cli_diag.InputReadError("ticker.map: not UTF-8 text")
        mod.main = main
        sys.modules[mod.__name__] = mod
        try:
            r = run_cmd(self._cmd("_zz_errA_unreadable"), capture_output=True)
        finally:
            del sys.modules[mod.__name__]
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("error: ticker.map: not UTF-8 text", r.stderr)

    def test_broken_pipe_in_process_is_not_a_traceback(self):
        from taxjson.lib.dispatch import run_cmd
        mod = types.ModuleType("taxjson.bin._zz_errA_pipe")

        def main():
            raise BrokenPipeError(32, "Broken pipe")
        mod.main = main
        sys.modules[mod.__name__] = mod
        try:
            r = run_cmd(self._cmd("_zz_errA_pipe"), capture_output=True)
        finally:
            del sys.modules[mod.__name__]
        self.assertEqual(r.returncode, cli_diag.BROKEN_PIPE_EXIT)
        self.assertNotIn("Traceback", r.stderr)

    def test_reconcile_slips_main_is_guarded(self):
        # `taxjson reconcile-slips` calls this main directly.
        from taxjson.bin import taxjson_reconcile_slips as RS
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc()))
            s = Path(td) / "slips.csv"
            s.write_text("symbol,quantity,proceeds\nZZQ.TO,10,120\n")
            m = Path(td) / "ticker.map"
            m.write_bytes(b"GLOBAL Z\xe9Q.TO ZZQ.TO\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                RS.main([str(s), "--gains", str(g), "--country", "canada",
                         "--ticker-map", str(m)])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("not UTF-8", err.getvalue())

    def test_subprocess_mode_goes_through_the_trampoline(self):
        from taxjson.lib.dispatch import run_cmd
        with tempfile.TemporaryDirectory() as td:
            loop = Path(td) / "loop.json"
            loop.symlink_to("loop.json")
            os.environ["TAXJSON_DISPATCH"] = "subprocess"
            try:
                r = run_cmd(self._cmd("taxjson_gains", "--country", "canada",
                                      loop), capture_output=True)
            finally:
                del os.environ["TAXJSON_DISPATCH"]
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("taxjson-gains: error: cannot read", r.stderr)


class TestExitCodes(unittest.TestCase):
    """A2-1421 / A2-1435 / A2-1436: a missing or unreadable named input
    exits 2 (usage/environment), never 1 (a finding) or 0 (ignored)."""

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
            dmap = td / "distributions.map"
            dmap.write_text("")
            g = td / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc()))
            runs = {
                "taxjson_corp_actions": ("--brokerage", "ib", "--country",
                                         "canada", "--list", "--ticker-map",
                                         nope, csv_),
                "taxjson_apply_distributions": ("--map", dmap, "--ticker-map",
                                                nope, b),
                "taxjson_harvest": ("--no-ibkr", "--country", "canada",
                                    "--ticker-map", nope, g),
            }
            for mod, args in runs.items():
                r = _tool(mod, *args, env={"TAXJSON_OFFLINE": "1"})
                self.assertEqual(r.returncode, 2, (mod, r.stderr))
                self.assertIn("--ticker-map", r.stderr, mod)


class TestOutputPaths(unittest.TestCase):
    """A2-1428 / A2-1432: an unwritable output says 'cannot write'."""

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
                          "--suggest-phantoms", out, b)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("cannot write", r.stderr)


class TestManifest(unittest.TestCase):
    """A2-0160 / A2-0463 / A2-1402 / A2-0804 / A2-1399 / A2-1401 /
    A2-1449: the elections manifest is read one way, in one line."""

    def _load(self, p):
        from taxjson.lib.corp_actions import Manifest
        return Manifest.load(p)

    def test_directory_and_loop_are_manifest_errors(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "manifest.json"
            d.mkdir()
            with self.assertRaisesRegex(ManifestError, "cannot be read"):
                self._load(d)
            loop = Path(td) / "loop.json"
            loop.symlink_to("loop.json")
            with self.assertRaisesRegex(ManifestError, "cannot be read"):
                self._load(loop)
            dangling = Path(td) / "gone.json"
            dangling.symlink_to("elsewhere.json")
            with self.assertRaises(ManifestError):
                self._load(dangling)
            self.assertEqual(self._load(Path(td) / "absent.json").records,
                             {})

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads a chmod-000 file")
    def test_unreadable_is_a_manifest_error(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text('{"elections": {}}')
            p.chmod(0)
            try:
                with self.assertRaises(ManifestError):
                    self._load(p)
            finally:
                p.chmod(0o600)

    def test_bom_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_bytes(b'\xef\xbb\xbf{"elections": {"e1": {"election": '
                          b'"ignore"}}}')
            self.assertEqual(self._load(p).records["e1"].election, "ignore")

    def test_non_string_summary_is_refused(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text(json.dumps({"elections": {"e1": {
                "election": "ignore", "summary": 7}}}))
            with self.assertRaisesRegex(ManifestError, "'summary'"):
                self._load(p)
            p.write_text(json.dumps({"elections": {"e1": {
                "election": "ignore", "summary": None, "notes": None}}}))
            rec = self._load(p).records["e1"]
            self.assertEqual((rec.summary, rec.notes), ("", ""))

    def test_spinoffs_view_refuses_in_one_line(self):
        from taxjson.lib.corp_views import ViewError, _manifest
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "inputs" / "margin" / "manifest.json"
            p.parent.mkdir(parents=True)
            for doc in ([], {"elections": {"e1": "rollover"}},
                        {"elections": {"e1": {"election": "x",
                                              "summary": 7}}}):
                p.write_text(json.dumps(doc))
                with self.assertRaises(ViewError):
                    _manifest(Path(td), "margin")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root writes a read-only folder")
    def test_save_to_a_read_only_folder_raises_oserror(self):
        # An OSError, which the commands report as one 'cannot write'
        # line with exit 2 (A2-1418); the old manifest is untouched.
        from taxjson.lib.corp_actions import Manifest
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "ro"
            d.mkdir()
            d.chmod(0o500)
            try:
                with self.assertRaises(OSError):
                    Manifest({}).save(d / "manifest.json")
            finally:
                d.chmod(0o700)

    def test_resolve_manifest_refuses_a_looping_link(self):
        # Never written through (A2-0160 ELOOP traceback, A2-1400).
        from taxjson.bin.taxjson_run import _resolve_manifest
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / "manifest.json").symlink_to("manifest.json")
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                _resolve_manifest(acct, Path(td) / "work", "margin",
                                  create=True)
        self.assertEqual(cm.exception.code, 2)


class TestBomJson(unittest.TestCase):
    """A2-0776 / A2-1412 / A2-1449: a BOM before hand-edited JSON is
    dropped by every JSON loader, as by the TOML and text readers."""

    def _bom(self, td, name, doc):
        p = Path(td) / name
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc).encode())
        return p

    def test_loaders_accept_a_bom(self):
        from taxjson.lib.core import load_transactions
        from taxjson.lib.json_input import read_json_doc
        from taxjson.lib.phantom_holdings import load_phantoms
        from taxjson.lib.report_model import load_report_json
        with tempfile.TemporaryDirectory() as td:
            book = self._bom(td, "b.json", {"transactions": [_base_row()]})
            self.assertEqual(len(read_json_doc(book)["transactions"]), 1)
            self.assertEqual(len(load_transactions(book)), 1)
            self.assertIn("transactions", load_report_json(book))
            ph = self._bom(td, "phantoms.json",
                           [{"symbol": "ZZQ.TO", "account": "margin"}])
            self.assertTrue(load_phantoms(ph))

    def test_merge_tools_accept_a_bom(self):
        with tempfile.TemporaryDirectory() as td:
            book = self._bom(td, "b.json", {"transactions": [_base_row()]})
            for mod in ("taxjson_merge", "taxjson_merge2"):
                r = _tool(mod, book)
                self.assertEqual(r.returncode, 0, (mod, r.stderr))
                self.assertEqual(len(json.loads(r.stdout)["transactions"]),
                                 1, mod)


class TestWorkDocShapes(unittest.TestCase):
    """A2-0794 / A2-1408 / A2-0793: read views and tools read work/
    documents through read_work_doc: one line naming the file."""

    def test_check_dates_rows(self):
        from taxjson.lib.check_dates import _rows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_questrade.json"
            self.assertEqual(_rows(p), [])
            for doc in (None, {"transactions": ["x"]},
                        {"transactions": 5},
                        {"transactions": [_base_row(price="x")]}):
                p.write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError,
                                            "margin_questrade.json"):
                    _rows(p)

    def test_edge_cases_rows(self):
        from taxjson.lib.edge_cases import _rows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_base.json"
            for doc in ({"transactions": ["x"]}, {"transactions": 5},
                        {"transactions": [_base_row(quantity="x")]}):
                p.write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError, "margin_base.json"):
                    _rows(p)
            p.write_text(json.dumps([_base_row()]))
            self.assertEqual(len(_rows(p)), 1)

    def test_tools_refuse_a_text_number(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            g = td / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc(inv=[{
                "symbol": "ZZQ.TO", "qty": "x", "total_cost": 5.0,
                "currency": "CAD"}])))
            b = td / "margin_base.json"
            b.write_text(json.dumps({"transactions": [
                _base_row(quantity="x")]}))
            dmap = td / "distributions.map"
            dmap.write_text("")
            runs = {
                "taxjson_harvest": ("--no-ibkr", "--country", "canada", g),
                "taxjson_apply_distributions": ("--map", dmap, b),
                "taxjson_split_gains": ("--account", "margin", g),
            }
            for mod, args in runs.items():
                r = _tool(mod, *args, env={"TAXJSON_OFFLINE": "1"})
                self.assertNotIn("Traceback", r.stderr, mod)
                self.assertEqual(r.returncode, 2, (mod, r.stderr))
                self.assertIn("not a number", r.stderr, mod)


if __name__ == "__main__":
    unittest.main()

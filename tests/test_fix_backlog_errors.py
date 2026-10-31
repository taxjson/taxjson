"""Backlog group `errors` (2026-10-25): error messages, atomic writes and
the smaller commands (elect, fetch --list, slips, t1135, tax-logic).

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account 99900001.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


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
    itself still needs one."""

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


SPIN = "20240603-parnt-spnco-3509"      # the demo's spin-off (synthetic)


def _demo(tmp):
    """`init --demo` (the years layout: exports shared in inputs/, a
    saved spin-off election); the 2024 year folder."""
    r = _tjs(None, "init", "--demo", str(Path(tmp) / "demo"))
    assert r.returncode == 0, r.stderr
    return Path(tmp) / "demo" / "2024"


class TestElect(unittest.TestCase):
    """TJ-0037 and TJ-0176: `tjs elect` in the years layout."""

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


class TestReconcileSlipsNoSlipYet(unittest.TestCase):
    """TJ-0046: with no slip saved the t5008 step is to do, not
    flagged: the refusal names a plain `--done t5008`, no --force and no
    'skip' wording."""

    def test_message(self):
        import contextlib
        import io
        from taxjson.bin import taxjson_reconcile_slips as RS
        with tempfile.TemporaryDirectory() as d:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                RS.main([str(Path(d) / "inputs" / "slips" / "*.csv"),
                         "--gains", str(Path(d) / "g.json"),
                         "--country", "canada"])
            self.assertEqual(cm.exception.code, 2)
            text = " ".join(err.getvalue().split())
            self.assertIn("nothing to reconcile yet", text)
            self.assertIn("`tjs checklist --done t5008`", text)
            self.assertNotIn("--force", text)
            self.assertNotIn("skip", text)


def _slip_report(suggest=False):
    """A synthetic slip-audit report (lib/slip_audit.audit's shape): two
    accounts with one USD T5 each, one difference, an income on no slip;
    `suggest`: a T5 box-18 suggestion too."""
    def bucket(slip, books):
        return {"currency": "USD", "slips": ["T5 ZZB"], "split_by_t3": [],
                "lines": [{"label": "Foreign income", "boxes": "T5 15",
                           "slip": slip, "books": books,
                           "diff": books - slip,
                           "status": "ok" if slip == books else "differs",
                           "books_cad_daily": books * 1.35,
                           "slip_cad_annual_average": slip * 1.36}]}

    def account(name, slip, books):
        return {"account": name, "groups": [{
            "broker_account": None, "buckets": [bucket(slip, books)],
            "payments": {"missing_from_books": [], "missing_from_slip": [],
                         "amount_differs": [], "other_year": []},
            "record_year": []}]}
    sg = {"capital_gains_dividends": [], "tt_lines": [], "notes": []}
    if suggest:
        sg["capital_gains_dividends"] = [{"toml": "[[distributions]]\n"
                                                  "symbol = \"ZZQ.TO\""}]
    return {"year": 2024, "tolerance": 1.0,
            "sources": [{"kind": "slips.toml",
                         "file": "inputs/slips/slips.toml", "slips": 2}],
            "annual_average": {},
            "accounts": [account("ib", 75.0, 75.0),
                         account("questrade", 30.0, 18.0)],
            "coverage": {"no_slip": [{"account": "margin",
                                      "sources": ["zz_demo.csv"],
                                      "income": {"CAD": 68.0}}],
                         "slips_without_books": []},
            "t5008": [], "suggestions": sg, "problems": [],
            "issues": [{"account": "questrade"}, {"account": "margin"}]}


class TestSlipAuditClosing(unittest.TestCase):
    """TJ-0179: "apply the suggestions" only when a SUGGESTIONS section
    was printed; the default view within the 6-line budget."""

    def test_no_suggestions(self):
        from taxjson.lib import out
        from taxjson.lib.slip_audit import render
        text = "\n".join(render(_slip_report(), width_=0, details=True))
        self.assertNotIn("SUGGESTIONS", text)
        self.assertNotIn("suggestion", text)
        self.assertIn("2 finding(s) (margin 1, questrade 1): correct the "
                      "books or slips.toml where one is wrong, or explain "
                      "each difference and mark the checklist's t5-t3 step "
                      "done.", text)
        brief = "\n".join(render(_slip_report(), width_=100))
        self.assertLessEqual(len(out.prose_lines(brief)), 6, brief)
        self.assertIn("): correct or explain each — tjs slip-audit "
                      "--details", brief)
        self.assertIn("USD:  T5 ZZB", brief)

    def test_with_suggestions(self):
        from taxjson.lib.slip_audit import render
        text = "\n".join(render(_slip_report(suggest=True), width_=0,
                                details=True))
        self.assertIn("SUGGESTIONS", text)
        self.assertIn("apply the suggestions, or explain each difference",
                      text)
        brief = "\n".join(render(_slip_report(suggest=True), width_=100))
        self.assertIn("): apply or explain each — tjs slip-audit --details",
                      brief)


class TestT1135Overrides(unittest.TestCase):
    """TJ-0083: the notes write the override with its keyword; a line
    with none is refused naming what to write (`tjs migrate` only
    when a t1135.map is there); the checklist's blocked line names
    `tjs t1135`, not migrate, and shows the problem, not the path."""

    def _refusal(self, text, t1135_map=False):
        from taxjson.bin.taxjson_t1135 import load_overrides
        from taxjson.lib.cli_diag import InputContentError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(text)
            if t1135_map:
                (Path(td) / "t1135.map").write_text("ZZQ.US CA\n")
            with self.assertRaises(InputContentError) as cm:
                load_overrides(p)
            msg = str(cm.exception)
            self.assertNotIn(td, msg)
        return msg

    def test_no_keyword_line(self):
        msg = self._refusal("TOBASE ZZQ.US ZZQ.TO\nZZC CA\n")
        self.assertTrue(msg.startswith(
            "ticker.map:2: 'ZZC CA' has no ticker.map keyword — "), msg)
        self.assertIn("`T1135 ZZC CA`", msg)
        self.assertNotIn("migrate", msg)
        msg = self._refusal("FOOBAR ZZQ.US ZZQ.TO\n")
        self.assertIn("`T1135 SYMBOL COUNTRY`", msg)
        self.assertIn("fix it or delete it", msg)
        self.assertNotIn("migrate", msg)

    def test_migrate_only_with_an_old_t1135_map(self):
        msg = self._refusal("ZZC CA\n", t1135_map=True)
        self.assertIn("`tjs migrate` converts the old t1135.map", msg)

    def test_other_problems_keep_their_text(self):
        msg = self._refusal("T1135 ZZQ.US CDN\n")
        self.assertIn("ISO 3166", msg)
        self.assertIn("`T1135 SYMBOL COUNTRY`", msg)
        self.assertNotIn("migrate", msg)

    def test_notes_name_the_keyword(self):
        import contextlib
        import io
        import json
        from taxjson.bin.taxjson_t1135 import build_report, render_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": []}))
            with contextlib.redirect_stderr(io.StringIO()):
                rep = build_report([base], [], 2025, {}, "CAD",
                                   full_history=False)
        rep["crypto_symbols"] = ["ZZC"]
        text = " ".join(render_report(rep).split())
        self.assertIn("a Canadian corp on a US exchange is not "
                      "(`T1135 SYMBOL CA`)", text)
        self.assertIn("(`T1135 SYMBOL CA` once checked)", text)
        self.assertNotIn("(`SYMBOL CA", text)

    def test_checklist_blocked_line_names_t1135(self):
        from types import SimpleNamespace
        from taxjson.lib import checklist as CL
        err = ("taxjson-t1135: error: ticker.map:54: 'ZZC CA' has no "
               "ticker.map keyword — a T1135 override is a `T1135 SYMBOL "
               "COUNTRY` line (`T1135 ZZC CA`); another line: fix it or "
               "delete it (`tjs migrate` converts the old t1135.map)\n")
        ctx = SimpleNamespace(settings={"country": "canada"},
                              sub=lambda *a: (2, "", err))
        r = CL.d_t1135(ctx)
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.cmd, "tjs t1135")
        self.assertTrue(r.detail.startswith("ticker.map:54: 'ZZC CA'"),
                        r.detail)


class TestEncodingAndSyntaxErrors(unittest.TestCase):
    """TJ-0123: an input's encoding or TOML syntax error names the file
    the way the run names inputs (never the absolute path), the headline
    on the label's line, the fix a detail; a hand-typed .tt is not told
    to re-export it."""

    def _group(self, name, data):
        from taxjson.bin.taxjson_run import group_inputs_detailed
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
        from taxjson.bin.taxjson_run import _stage_text_shown
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


_DEV_FULL = "/dev/full"       # every write: ENOSPC (Linux); no disk filled


def _fill_disk(f):
    """Make every later write through `f`'s descriptor fail as on a full
    disk: /dev/full put in its place (never a real disk filled)."""
    fd = os.open(_DEV_FULL, os.O_WRONLY)
    try:
        os.dup2(fd, f.fileno())
    finally:
        os.close(fd)


@unittest.skipUnless(os.path.exists(_DEV_FULL), "needs /dev/full")
class TestFullDisk(unittest.TestCase):
    """TJ-0106: on a full disk the message names the file and the fix,
    once; a failed stage keeps its "stopped at" line."""

    def test_describe_names_the_output_and_the_fix(self):
        import errno
        from taxjson.lib.cli_diag import describe_input_error
        msg = describe_input_error(OSError(errno.EFBIG, "File too large"))
        self.assertEqual(msg, "cannot write the output: File too large "
                              "(disk full?) — free some space on the disk "
                              "and run again")
        e = OSError(errno.ENOSPC, "No space left on device")
        e.filename = "/zz/nowhere/work/x.json"
        self.assertTrue(describe_input_error(e).startswith(
            "cannot write /zz/nowhere/work/x.json: No space left on "
            "device (disk full?)"))

    def test_said_once_by_nested_guards(self):
        import contextlib
        import io
        from taxjson.lib.cli_diag import guard_main
        with tempfile.TemporaryDirectory() as td:
            with open(Path(td) / "out.json", "w") as out:
                def main():
                    print("x" * 64)
                    _fill_disk(out)
                err = io.StringIO()
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err), \
                        self.assertRaises(SystemExit) as cm:
                    guard_main("taxjson-zz")(guard_main("taxjson-zz")(main))()
                self.assertEqual(cm.exception.code, 2)
                try:
                    out.close()
                except OSError:
                    pass
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("cannot write the output: No space left on device "
                      "(disk full?)", lines[0])

    def test_atomic_open_keeps_the_block_error(self):
        import subprocess as sp
        from taxjson.lib.safe_write import atomic_open
        with tempfile.TemporaryDirectory() as td:
            final = Path(td) / "zz.json"
            final.write_text("old")
            with self.assertRaises(sp.CalledProcessError):
                with atomic_open(final) as f:
                    f.write("new" * 10)
                    _fill_disk(f)
                    raise sp.CalledProcessError(2, ["zz"])
            self.assertEqual(final.read_text(), "old")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["zz.json"])

    def test_atomic_open_names_the_file(self):
        from taxjson.lib.safe_write import atomic_open
        with tempfile.TemporaryDirectory() as td:
            final = Path(td) / "zz.json"
            final.write_text("old")
            with self.assertRaises(OSError) as cm:
                with atomic_open(final) as f:
                    f.write("new" * 10)
                    _fill_disk(f)
            self.assertEqual(cm.exception.filename, str(final))
            self.assertEqual(final.read_text(), "old")

    def _run_to_file(self, fake):
        import contextlib
        import io
        import subprocess as sp
        from unittest import mock
        from taxjson.bin import taxjson_run as TR
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "work" / "margin_zz.json"
            out.parent.mkdir()
            out.write_text("old")
            err = io.StringIO()
            with mock.patch("taxjson.lib.dispatch.run_cmd", fake), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(sp.CalledProcessError) as cm:
                TR.run_to_file(["python", "-m", "taxjson.bin.taxjson_brokerage",
                                "--account", "margin"], out)
            self.assertEqual(out.read_text(), "old")
        return cm.exception, err.getvalue()

    def test_failed_stage_names_the_work_file(self):
        import subprocess as sp
        from taxjson.bin import taxjson_run as TR
        line = ("taxjson-brokerage: error: cannot write the output: File "
                "too large (disk full?) — free some space on the disk and "
                "run again\n")

        def fake(cmd, stdout=None, **kw):
            return sp.CompletedProcess(cmd, 2, stdout=None, stderr=line)
        e, shown = self._run_to_file(fake)
        self.assertEqual(shown.count("cannot write"), 1, shown)
        self.assertIn("cannot write work/margin_zz.json: File too large "
                      "(disk full?)", shown)
        why = TR._failure_headline(TR._stage_text_shown(
            e.stderr, e.cmd, getattr(e, "out_path", None)))
        self.assertEqual(why, "cannot write work/margin_zz.json: File too "
                              "large (disk full?)")
        self.assertIn("for account margin", TR._stage_description(e.cmd))

    def test_the_runs_own_write_names_the_work_file(self):
        import subprocess as sp

        def fake(cmd, stdout=None, **kw):
            stdout.write("{}" * 10)
            _fill_disk(stdout)
            return sp.CompletedProcess(cmd, 0, stdout=None, stderr="")
        e, shown = self._run_to_file(fake)
        self.assertEqual(e.returncode, 2)
        self.assertIn("cannot write work/margin_zz.json: No space left on "
                      "device (disk full?)", e.stderr)
        self.assertIn("(disk full?) Free some space on the disk and run "
                      "again.", " ".join(shown.split()))


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_MINIMAL_TOML = '[settings]\ncountry = "canada"\nyear = 2024\n'


def _locked(path, mode):
    """chmod `path` to `mode`; the cleanup puts 0o755 / 0o644 back while
    it exists (TemporaryDirectory removes a read-only tree itself)."""
    path.chmod(mode)

    def _unlock():
        if path.exists():
            path.chmod(0o755 if path.is_dir() else 0o644)
    return _unlock


class TestWriteFailuresNameTheFile(unittest.TestCase):
    """TJ-0056: a write refused names the file asked for (never the temp
    or backup file beside it), the reason and the fix."""

    def test_cannot_write_texts(self):
        import errno
        from taxjson.lib.cli_diag import cannot_write
        self.assertEqual(
            cannot_write("out/x.csv", OSError(errno.ENOSPC, "No space left "
                                              "on device")),
            ("cannot write out/x.csv: No space left on device (disk full?)",
             "Free some space on the disk."))
        self.assertEqual(
            cannot_write("x.csv", OSError(errno.EROFS, "Read-only file "
                                          "system"), alt=", or zz")[1],
            "The disk it is on is mounted read-only, or zz.")
        self.assertEqual(
            cannot_write("x.csv", OSError(errno.EIO, "I/O error")),
            ("cannot write x.csv: I/O error", ""))

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_xlsx_to_csv_into_a_read_only_folder(self):
        import contextlib
        import io
        import sys
        import types
        from unittest import mock
        from taxjson.bin import xlsx_to_csv as X

        class _Frame:
            def to_csv(self, *a, **k):
                return "a,b\n1,2\n"
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "zz.xlsx").write_bytes(b"")
            ro = Path(td) / "rodir"
            ro.mkdir()
            self.addCleanup(_locked(ro, 0o555))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            err = io.StringIO()
            with mock.patch.object(X, "read_sheet",
                                   lambda f, s: _Frame()), \
                    mock.patch.object(X, "_apply_cells", lambda d, fn: d), \
                    mock.patch.dict(sys.modules,
                                    {"pandas": types.ModuleType("pandas")}), \
                    contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                X.convert_xlsx_to_csv("zz.xlsx", "rodir/x.csv")
            self.assertEqual(cm.exception.code, 2)
            text = " ".join(err.getvalue().split())
            self.assertIn("cannot write rodir/x.csv: Permission denied",
                          text)
            self.assertIn("The folder rodir/ is not writable: `chmod u+w "
                          "rodir`, or choose another -o.", text)
            self.assertNotIn(".part", text)
            self.assertEqual(list(ro.iterdir()), [])

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_format_in_a_read_only_folder(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            (proj / "taxjson.toml").write_text(_MINIMAL_TOML)
            self.addCleanup(_locked(proj, 0o555))
            r = _tjs(None, "-C", "proj", "format", "--write", cwd=td)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("cannot write proj/taxjson.toml: Permission "
                          "denied", r.stderr)
            self.assertIn("The folder proj/ is not writable: `chmod u+w "
                          "proj`. Nothing was changed.", r.stderr)
            self.assertNotIn(".bak", r.stderr)
            self.assertEqual((proj / "taxjson.toml").read_text(),
                             _MINIMAL_TOML)

    @unittest.skipIf(_ROOT, "root ignores permissions")
    def test_run_with_a_read_only_work_folder(self):
        with tempfile.TemporaryDirectory() as td:
            year = _demo(td)
            work = year / "work"
            work.mkdir(exist_ok=True)
            self.addCleanup(_locked(work, 0o555))
            r = _tjs(None, "-C", "demo/2024", "run", cwd=td)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("cannot write to demo/2024/work/: Permission "
                          "denied", r.stderr)
            self.assertIn("`chmod u+w demo/2024/work` makes it writable. "
                          "Nothing was run.", r.stderr)


@unittest.skipIf(_ROOT, "root ignores permissions")
class TestReadOnlyUserFile(unittest.TestCase):
    """TJ-0107: a user file made read-only (a filed year's taxjson.toml)
    is refused, not replaced; a generated file is still replaced."""

    def test_format_refuses_a_read_only_toml(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "taxjson.toml"
            cfg.write_text(_MINIMAL_TOML)
            self.addCleanup(_locked(cfg, 0o444))
            r = _tjs(None, "format", "--write", cwd=td)
            self.assertEqual(r.returncode, 2, r.stderr)
            text = " ".join(r.stderr.split())
            self.assertIn("cannot write taxjson.toml: the file is "
                          "read-only", text)
            self.assertIn("`chmod u+w taxjson.toml` lets taxjson rewrite "
                          "it. Nothing was changed.", text)
            self.assertEqual(cfg.read_text(), _MINIMAL_TOML)
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["taxjson.toml"])

    def test_safe_write_user_files(self):
        from taxjson.lib.safe_write import (ReadOnlyFileError, write_atomic,
                                            write_user_file)
        with tempfile.TemporaryDirectory() as td:
            tm = Path(td) / "ticker.map"
            tm.write_text("old\n")
            self.addCleanup(_locked(tm, 0o444))
            with self.assertRaises(ReadOnlyFileError) as cm:
                write_user_file(tm, "new\n", td)
            self.assertEqual(cm.exception.filename, str(tm))
            with self.assertRaises(ReadOnlyFileError):
                write_atomic(tm, "new\n", keep_mode=True)
            self.assertEqual(tm.read_text(), "old\n")
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["ticker.map"])
            # A generated file (no keep_mode) is replaced as before.
            gen = Path(td) / "zz.json"
            gen.write_text("old")
            self.addCleanup(_locked(gen, 0o444))
            write_atomic(gen, "new")
            self.assertEqual(gen.read_text(), "new")

    def test_guard_names_the_file_and_the_fix(self):
        from taxjson.lib.cli_diag import describe_input_error
        from taxjson.lib.safe_write import refuse_read_only
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "slips.toml"
            p.write_text("")
            self.addCleanup(_locked(p, 0o444))
            here = os.getcwd()
            os.chdir(td)
            self.addCleanup(os.chdir, here)
            with self.assertRaises(OSError) as cm:
                refuse_read_only(p)
            self.assertEqual(describe_input_error(cm.exception),
                             "cannot write slips.toml: the file is "
                             "read-only — `chmod u+w slips.toml` lets "
                             "taxjson rewrite it")


class TestTaxLogicOutsideAProject(unittest.TestCase):
    """TJ-0187: outside a project the title says "default settings",
    never "with this project's settings"."""

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

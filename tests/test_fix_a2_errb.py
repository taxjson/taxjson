"""Regression pins for the re-audit-2 error-handling lists errors-02 and
errors-05: one-line errors (never a traceback) and consistent exit codes
in `taxjson` (bin/taxjson_run.py), the console-script trampoline
(bin/_entry.py) and `taxjson watch`.

Exit-code rule (lib/cli_diag): 0 ok, 1 the command's finding (or a
refusal such as 'run `taxjson run` first'), 2 a named input or output
that cannot be read or written, 130 Ctrl-C, 141 a closed stdout pipe.

Synthetic projects only (account `m`, hand-written FX rates in work/,
TAXJSON_OFFLINE set): nothing here touches the network.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(os.environ.get("ERRB_REPO") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(REPO_ROOT / "src"))

from taxjson.lib import cli_diag  # noqa: E402


def _env(home, **kw):
    e = {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
         "NO_COLOR": "1", "PYTHONPATH": str(REPO_ROOT / "src")}
    e.update(kw)
    return e


def _cli(root, home, *a, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=str(root), capture_output=True, text=True, stdin=stdin,
        env=env or _env(home))


def _tool(module, *a, cwd, home):
    code = ("import sys; sys.argv[0] = 'taxjson-x'; "
            f"from taxjson.bin._entry import {module} as f; "
            "sys.exit(f())")
    return subprocess.run([sys.executable, "-c", code, *a], cwd=str(cwd),
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=_env(home))


def _rates(path: Path):
    d, lines = date(2024, 1, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _make_project(root: Path, *, extra_accounts=""):
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "inputs" / "m" / "a.tt").write_text(
        "BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
        "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2025\n\n'
        '[accounts.m]\ntype = "taxable"\n' + extra_accounts)
    _rates(root / "work" / "to_base.csv")


class _Built(unittest.TestCase):
    """One synthetic project, built once; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.home = td / "home"
        cls.home.mkdir()
        cls.base = td / "base"
        _make_project(cls.base)
        r = _cli(cls.base, cls.home, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self) -> Path:
        d = Path(tempfile.mkdtemp(dir=self._td.name))
        root = d / "p"
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def cli(self, root, *a, **kw):
        return _cli(root, self.home, *a, **kw)

    def assertOneLine(self, r, rc, needle=None):
        self.assertNotIn("Traceback", r.stderr + r.stdout)
        self.assertEqual(r.returncode, rc, r.stderr[-2000:])
        if needle:
            self.assertIn(needle, r.stderr)


# ------------------------------------------------ handoff prior-year record
class TestHandoffRecord(_Built):
    """A2-0162, A2-0473, A2-0475, A2-0476, A2-0802, A2-0164."""

    def _handoff(self, root, body: bytes):
        rp = root / "prior.json"
        rp.write_bytes(body)
        return self.cli(root, "handoff", "--prior", str(rp))

    def test_unreadable_or_wrong_shape_record_is_one_line_exit_2(self):
        root = self.copy()
        rec = {"schema_version": 2, "year": 2024, "country": "canada",
               "year_end": {}, "settle_next_year": [], "dispositions": []}
        for body in (b"x{", b"", b"\xef\xbb\xbf{}", b"\xff\xfe\x00",
                     b"[1, 2]", b"null",
                     json.dumps(dict(rec, year="x")).encode(),
                     json.dumps(dict(rec, year_end={"equity": []})).encode(),
                     json.dumps(dict(rec, year_end={"equity": {
                         "ABC.TO": {"qty": "abc", "acb": 1}}})).encode(),
                     json.dumps(dict(rec, settle_next_year=[
                         {"symbol": "ABC.TO", "qty": 1.0}])).encode(),
                     json.dumps(dict(rec, dispositions=[
                         {"symbol": "ABC.TO", "qty": 1.0,
                          "proceeds": 5.0}])).encode()):
            with self.subTest(body=body[:60]):
                r = self._handoff(root, body)
                self.assertOneLine(r, 2, "prior.json")

    def test_directory_and_missing_record_exit_2(self):
        root = self.copy()
        (root / "pd").mkdir()
        self.assertOneLine(self.cli(root, "handoff", "--prior",
                                    str(root / "pd")), 2, "pd")
        self.assertOneLine(self.cli(root, "handoff", "--prior",
                                    "nope.json"), 2,
                           "no prior-year record at nope.json")

    def test_wrong_type_corporate_distributions_is_one_line(self):
        root = self.copy()
        cfg = (root / "taxjson.toml").read_text().replace(
            "[settings]\n", '[settings]\ncorporate_distributions = '
                            '"XYZ.TO"\n')
        (root / "taxjson.toml").write_text(cfg)
        rec = {"schema_version": 2, "year": 2024, "country": "canada",
               "year_end": {}, "settle_next_year": [], "dispositions": []}
        r = self._handoff(root, json.dumps(rec).encode())
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("corporate_distributions", r.stderr)


# ---------------------------------------------- run: project / work files
class TestRunInputs(_Built):

    def test_overrides_not_utf8_is_one_line_exit_2(self):
        """A2-0163, A2-0468, A2-0784, A2-1438."""
        root = self.copy()
        (root / "ticker_extraction_overrides.txt").write_bytes(b"\xe9x\n")
        self.assertOneLine(self.cli(root, "run", "--no-input"), 2,
                           "ticker_extraction_overrides.txt: not UTF-8")

    def test_overrides_directory_exit_2(self):
        root = self.copy()
        (root / "ticker_extraction_overrides.txt").mkdir()
        self.assertOneLine(self.cli(root, "run", "--no-input"), 2,
                           "ticker_extraction_overrides.txt is a directory")

    def test_non_utf8_work_stamps_are_rebuilt(self):
        """A2-0795, A2-1429: `run` is the recovery for work/ damage."""
        for name in ("m_inputs.fingerprint", "m_sources.list",
                     ".project_maps_applied", ".code_fingerprint"):
            with self.subTest(name=name):
                root = self.copy()
                (root / "ticker.map").write_text("GLOBAL XYZ.US XYZ.TO\n")
                (root / "work" / name).write_bytes(b"\xff\xfe junk")
                for a in (["run", "--no-input"],
                          ["run", "--fast", "--no-input"]):
                    r = self.cli(root, *a)
                    self.assertOneLine(r, 0)

    def test_check_dates_on_non_utf8_sources_list(self):
        root = self.copy()
        (root / "work" / "m_sources.list").write_bytes(b"\xff")
        r = self.cli(root, "check-dates")
        self.assertNotIn("Traceback", r.stderr)

    def test_directory_in_place_of_work_files(self):
        """A2-1433: one line naming the path, exit 2."""
        for name in (".code_fingerprint", "m_inputs.fingerprint"):
            with self.subTest(name=name):
                root = self.copy()
                p = root / "work" / name
                p.unlink(missing_ok=True)
                p.mkdir()
                self.assertOneLine(self.cli(root, "run", "--no-input"), 2,
                                   name)

    def test_cad_only_to_base_csv_directory(self):
        root = self.copy()
        cfg = (root / "taxjson.toml").read_text().replace(
            'source_currencies = ["USD"]\n', "source_currencies = []\n")
        (root / "taxjson.toml").write_text(cfg)
        (root / "work" / "to_base.csv").unlink()
        (root / "work" / "to_base.csv").mkdir()
        self.assertOneLine(self.cli(root, "run", "--no-input"), 2,
                           "to_base.csv")

    def test_damaged_holdings_snapshot(self):
        """A2-0792: the previous run's holdings report, damaged."""
        for body in ('holding = "x"\n',
                     '[[holding]]\nsymbol = "ABC.TO"\nquantity = "abc"\n',
                     None):
            with self.subTest(body=body):
                root = self.copy()
                f = root / "reports" / "m_holdings.toml"
                if body is None:
                    f.write_bytes(b"\xff\xfe\x00junk")
                else:
                    f.write_text(body)
                self.assertOneLine(self.cli(root, "run", "--no-input"), 0)
                if body is not None:
                    f.write_text(body)
                    r = self.cli(root, "scan")
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn("m_holdings.toml", r.stderr)

    def test_dangling_manifest_symlink_is_refused(self):
        """A2-1400: the user's elections are not overwritten."""
        root = self.copy()
        (root / "elsewhere").mkdir()
        target = root / "elsewhere" / "manifest.json"
        (root / "inputs" / "m" / "manifest.json").unlink(missing_ok=True)
        (root / "inputs" / "m" / "manifest.json").symlink_to(target)
        r = self.cli(root, "run", "--no-input")
        self.assertOneLine(r, 2, "manifest.json is a symlink")
        self.assertFalse(target.exists())

    def test_second_run_is_refused_while_one_holds_the_lock(self):
        """A2-0778."""
        root = self.copy()
        import fcntl
        with open(root / "work" / ".run.lock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = self.cli(root, "run", "--no-input")
        self.assertOneLine(r, 1, "another `taxjson run` is in progress")
        self.assertOneLine(self.cli(root, "run", "--no-input"), 0)


# ------------------------------------------- pending elections aggregate
class TestPendingElections(_Built):
    """A2-0471, A2-0779, A2-1419, A2-1439, A2-1457, A2-1460."""

    BODIES = (b'{"accounts": {', b"[]", b"\xff", b'"x"',
              b'{"accounts": [1]}', b'{"accounts": {"m": "x"}}',
              b'{"accounts": {"m": {"pending": [5]}}}',
              b'{"accounts": {"m": {"pending": "x"}}}')

    def test_elect_pending_one_line_exit_2(self):
        root = self.copy()
        p = root / "work" / "pending_elections.json"
        for body in self.BODIES:
            with self.subTest(body=body):
                p.write_bytes(body)
                for a in (["elect", "--pending"],
                          ["elect", "--pending", "--json"]):
                    self.assertOneLine(self.cli(root, *a), 2,
                                       "pending_elections.json")

    def test_run_account_drops_a_damaged_aggregate(self):
        root = self.copy()
        p = root / "work" / "pending_elections.json"
        for body in (b"[1, 2]", b'{"accounts": [1]}',
                     b'{"accounts": {"brk": "x"}}'):
            with self.subTest(body=body):
                p.write_bytes(body)
                r = self.cli(root, "run", "--account", "m", "--no-input")
                self.assertOneLine(r, 0)
                self.assertFalse(p.exists())

    def test_elect_set_falls_back_on_a_damaged_aggregate(self):
        root = self.copy()
        (root / "work" / "pending_elections.json").write_bytes(b"[]")
        r = self.cli(root, "elect", "m", "--set", "nope-1=ignore")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("nope-1", r.stderr)


# ----------------------------------------------------------- config shapes
class TestConfigShapes(_Built):

    def test_top_level_estimate_or_instalments_scalar(self):
        """A2-0469."""
        for line, cmds in (("estimate = 5", ("estimate", "sum")),
                           ('instalments = "x"', ("instalments",))):
            root = self.copy()
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(line + "\n" + cfg)
            for c in cmds:
                with self.subTest(line=line, cmd=c):
                    key = line.split()[0]
                    self.assertOneLine(self.cli(root, c), 1,
                                       f"[{key}] must be a table")

    def test_bom_toml_accepted_by_the_radar_family(self):
        """A2-0470, A2-1454, A2-1456, A2-1458, A2-1459."""
        root = self.copy()
        cfg = root / "taxjson.toml"
        cfg.write_bytes(b"\xef\xbb\xbf" + cfg.read_bytes())
        for a in (["wash-radar"], ["buy-check", "ABC.TO"],
                  ["sell-check", "ABC.TO"], ["watch"]):
            with self.subTest(cmd=a[0]):
                r = self.cli(root, *a)
                self.assertNotIn("cannot be read", r.stderr)
                self.assertNotIn("Traceback", r.stderr)


# ---------------------------------------------------- BOM'd user files
class TestBomUserFiles(_Built):
    """A2-1409, A2-1453."""

    def test_sanity_holdings_with_bom(self):
        root = self.copy()
        h = root / "broker_holdings.toml"
        h.write_bytes(b"\xef\xbb\xbf" + b'[[holding]]\nsymbol = "ABC.TO"\n'
                      b'quantity = 60\n')
        r = self.cli(root, "sanity", f"m={h}")
        self.assertNotIn("Invalid statement", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_export_holdings_toml_with_bom(self):
        root = self.copy()
        h = root / "h.toml"
        h.write_bytes(b"\xef\xbb\xbf" + b'[[holding]]\nsymbol = "ABC.TO"\n'
                      b'quantity = 60\ncurrency = "CAD"\n')
        r = _tool("taxjson_export", str(h), cwd=root, home=self.home)
        self.assertNotIn("not a readable TOML", r.stderr)

    def test_phantoms_with_bom(self):
        from taxjson.lib.missing_history import load_missing_history
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing_history.json"
            p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
                [{"symbol": "ABC.TO", "account": "m"}]).encode())
            self.assertEqual(load_missing_history(p), {("ABC.TO", "m")})


# -------------------------------------------- views, accounts, write errors
class TestViews(_Built):

    def test_path_or_pattern_account_is_refused(self):
        """A2-1398, A2-0798."""
        root = self.copy()
        for a in (["sum", "./m"], ["list", "../p/work/m"],
                  ["wash-radar", "../p/work/m"], ["fees-sum", "/"],
                  ["fees-sum", "*"], ["events", "./m"]):
            with self.subTest(a=a):
                self.assertOneLine(self.cli(root, *a), 1,
                                   "is not an account name")

    def test_never_run_or_empty_work_views_say_run_first(self):
        """A2-0797, A2-1395."""
        root = self.copy()
        shutil.rmtree(root / "work")
        shutil.rmtree(root / "reports")
        for c in ("spinoffs", "splits", "transfers"):
            with self.subTest(cmd=c):
                self.assertOneLine(self.cli(root, c), 1,
                                   "run `taxjson run` first")
        (root / "work").mkdir()
        (root / "work" / ".project_maps_applied").write_text("x\n")
        for c in ("edge-cases", "fees-sum", "spinoffs"):
            with self.subTest(cmd=c, work="empty"):
                self.assertOneLine(self.cli(root, c), 1,
                                   "run `taxjson run` first")

    def test_harvest_with_dangling_base_symlink(self):
        """A2-1422."""
        root = self.copy()
        (root / "work" / "zz_base.json").symlink_to(root / "nope")
        r = self.cli(root, "harvest")
        self.assertNotIn("Traceback", r.stderr)

    def test_scan_wrong_shape_stage_file(self):
        """A2-1440."""
        root = self.copy()
        (root / "ticker.map").write_text("GLOBAL XYZ.US XYZ.TO\n")
        for f in sorted((root / "work").glob("m_*tt*.json"))[:1] or [
                root / "work" / "m_raw.json"]:
            f.write_text('{"transactions": [1]}')
        r = self.cli(root, "scan")
        self.assertNotIn("Traceback", r.stderr)

    def test_list_date_fails_when_an_account_recompute_fails(self):
        """A2-1450."""
        root = self.copy()
        b = root / "work" / "m_base.json"
        doc = json.loads(b.read_text())
        doc["transactions"][0]["quantity"] = "abc"
        b.write_text(json.dumps(doc))
        r = self.cli(root, "list", "--date", "2025-06-30")
        self.assertOneLine(r, 1, "as-of compute failed for m")
        self.assertNotIn("no base files", r.stderr)

    def test_audit_one_line_and_same_code_with_json(self):
        """A2-0796."""
        root = self.copy()
        (root / "missing_history.json").write_text('{"x": "')
        for a in (["audit"], ["audit", "--json"]):
            with self.subTest(a=a):
                r = self.cli(root, *a)
                self.assertOneLine(r, 2)
                lines = [ln for ln in r.stderr.splitlines()
                         if "missing_history.json" in ln]
                self.assertEqual(len(lines), 1, r.stderr)
                self.assertNotIn("produced no JSON", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_read_only_inputs_folder_writes(self):
        """A2-1418 (elect), A2-1416 (crypto-sends: covered in lib)."""
        root = self.copy()
        d = root / "inputs" / "m"
        (d / "manifest.json").write_text('{"elections": {}}\n')
        os.chmod(d, 0o500)
        try:
            r = self.cli(root, "elect", "m", "--reset")
        finally:
            os.chmod(d, 0o700)
        self.assertOneLine(r, 2, "cannot write")


class TestCryptoSendsReadOnly(unittest.TestCase):
    """A2-1416: an unwritable sends.json folder is one line, exit 2."""

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_set_with_read_only_inputs(self):
        from test_fix_sends import TAO_ID, _cli as _scli, _project
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            r = _scli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            d = root / "inputs" / "crypto"
            os.chmod(d, 0o500)
            try:
                r = _scli(root, home, "crypto-sends", "crypto", "--set",
                          f"{TAO_ID}=payment")
            finally:
                os.chmod(d, 0o700)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("cannot write", r.stderr)
            self.assertFalse((d / "sends.json.part").exists())


# ------------------------------------------------------------------ init
class TestInit(unittest.TestCase):

    def test_init_under_c_locale_writes_utf8_config(self):
        """A2-0477."""
        with tempfile.TemporaryDirectory() as td:
            env = _env(td, LC_ALL="C", LANG="C", PYTHONCOERCECLOCALE="0",
                       PYTHONUTF8="0")
            env.pop("PYTHONIOENCODING", None)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "init",
                 "--country", "ca", "--year", "2025", str(Path(td) / "p")],
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = (Path(td) / "p" / "taxjson.toml").read_text(
                encoding="utf-8")
            self.assertIn("—", cfg)

    def test_init_with_inputs_as_a_file_writes_nothing(self):
        """A2-0781."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p"
            p.mkdir()
            (p / "inputs").write_text("")
            r = _cli(p, td, "init", "--country", "ca", str(p))
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("inputs", r.stderr)
            self.assertFalse((p / "taxjson.toml").exists())
            self.assertFalse((p / "ticker.map").exists())


# ----------------------------------------------- exit codes (A2-0164)
class TestExitCodes(_Built):

    def test_missing_named_input_is_exit_2(self):
        root = self.copy()
        for a in (["sanity", "m=nope.toml"],
                  ["carryover", "--claimed", "nope.txt"],
                  ["handoff", "--prior", "nope.json"],
                  ["redact", "nope.csv"],
                  ["close-year", "--force", "--filed-dispositions",
                   "nope.csv"]):
            with self.subTest(a=a):
                self.assertOneLine(self.cli(root, *a), 2)
        r = _cli(root, self.home)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root / "nope"), "sum"], capture_output=True, text=True,
            env=_env(self.home), stdin=subprocess.DEVNULL)
        self.assertOneLine(r, 2, "no such directory")

    def test_standalone_tools_missing_input_exit_2(self):
        root = self.copy()
        nope = str(root / "nope.json")
        for mod, args in (("taxjson_merge", [nope]),
                          ("taxjson_merge2", [nope]),
                          ("taxjson_validate", [nope]),
                          ("taxjson_ccd_gains", [nope]),
                          ("taxjson_leaps_gains", [nope]),
                          ("taxjson_detect_brokerage", [nope]),
                          ("taxjson_missing_history", [nope]),
                          ("taxjson_convert_tt", [nope, str(root / "o.tt")])):
            with self.subTest(tool=mod):
                r = _tool(mod, *args, cwd=root, home=self.home)
                self.assertOneLine(r, 2)


# ------------------------------------------- the top-level process guard
class TestTopLevel(unittest.TestCase):
    """A2-0782, A2-1425 (Ctrl-C), A2-0785 (closed pipe), A2-0786
    (stdout that cannot encode), A2-1420 (main's input guard)."""

    def test_keyboard_interrupt_is_one_line_exit_130(self):
        def boom():
            raise KeyboardInterrupt
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cli_diag.run_top_level("taxjson-x", boom,
                                   interrupt_note=lambda: "redo it")
        self.assertEqual(cm.exception.code, 130)
        self.assertIn("taxjson-x: error: interrupted — redo it",
                      err.getvalue())

    def test_broken_pipe_exits_141_quietly(self):
        def boom():
            raise BrokenPipeError
        with mock.patch.object(cli_diag, "_stdout_closed_exit",
                               side_effect=SystemExit(141)):
            with self.assertRaises(SystemExit) as cm:
                cli_diag.run_top_level("t", boom)
        self.assertEqual(cm.exception.code, 141)

    def test_closed_pipe_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.Popen(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "tax-logic", "--country", "canada"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=_env(td), cwd=td,
                stdin=subprocess.DEVNULL)
            p.stdout.close()
            _, err = p.communicate(timeout=120)
            self.assertNotIn(b"Traceback", err)
            self.assertNotIn(b"BrokenPipeError", err)
            self.assertEqual(p.returncode, 141)

    def test_ctrl_c_in_a_console_script(self):
        with tempfile.TemporaryDirectory() as td:
            code = ("import sys; sys.argv[0] = 'taxjson-sort'; "
                    "from taxjson.bin._entry import taxjson_sort as f; "
                    "sys.exit(f())")
            p = subprocess.Popen([sys.executable, "-c", code],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=_env(td))
            time.sleep(1.5)
            import signal
            p.send_signal(signal.SIGINT)
            _, err = p.communicate(timeout=60)
            self.assertNotIn(b"Traceback", err)
            self.assertIn(b"interrupted", err)
            self.assertEqual(p.returncode, 130)

    def test_ascii_stdout_does_not_crash_a_report(self):
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "tax-logic", "--country", "canada"], capture_output=True, cwd=td,
                env=_env(td, PYTHONIOENCODING="ascii"),
                stdin=subprocess.DEVNULL)
            self.assertNotIn(b"UnicodeEncodeError", r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])

    def test_main_guard_turns_an_input_error_into_one_line(self):
        from taxjson.bin import taxjson_run as R
        import argparse

        def cmd(_args):
            Path("/nonexistent/dir/x.json").read_text()
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            R._guarded_func(argparse.Namespace(cmd="demo", func=cmd))
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("taxjson demo: error: no such file", err.getvalue())


# ----------------------------------------- checklist --walk prompts
class TestChecklistWalkPrompts(unittest.TestCase):
    """A2-1394: EOF or Ctrl-C at the note prompt keeps earlier marks."""

    def test_eof_at_note_prompt(self):
        from taxjson.bin import taxjson_run as R
        cl = mock.Mock()
        cl.STEPS = [("s1",), ("s2",)]
        res = mock.Mock(passed=False, effective="open", detail="x",
                        override=None, note=None)
        cl.evaluate.return_value = [res]
        cl.step_meta.return_value = (None, 1, "T", "cmd", "why")
        cl.STAGES = [(1, "stage")]
        cl.SYMBOL = {"open": "-"}
        ctx = mock.Mock(root=Path("/tmp/x"), year=2025,
                        settings={"country": "canada"})
        answers = iter(["d"])

        def fake_input(prompt=""):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError
        err = io.StringIO()
        with mock.patch("builtins.input", fake_input), \
                redirect_stdout(io.StringIO()), redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            R._checklist_walk(ctx, cl, None)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("interrupted at s1", err.getvalue())
        cl.set_override.assert_not_called()


# ------------------------------------------------------------ watch state
class TestWatchState(unittest.TestCase):
    """A2-1460: a nested wrong shape re-baselines with the warning."""

    def test_nested_wrong_shape(self):
        from taxjson.bin import taxjson_watch as W
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            for radar in (["x"], {"XEI.TO": "x"},
                          {"XEI.TO": {"category": 5}}):
                p.write_text(json.dumps({"schema_version": 1,
                                         "radar": radar}))
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertIsNone(W.load_state(p))
                self.assertIn("NEW baseline", err.getvalue())


# ------------------------------------------------------ fetch helpers
class TestFetchHelpers(unittest.TestCase):

    def test_merge_csv_with_bom(self):
        """A2-1445."""
        from taxjson.bin.taxjson_run import _merge_csv_text
        qt = "Transaction Date,Action,Symbol\n2025-01-02,Buy,ABC\n"
        merged, added = _merge_csv_text("﻿" + qt, qt)
        self.assertEqual(added, 0)

    def test_ib_flex_text_with_bom_is_decoded(self):
        """A2-0799: fetch decodes like detection (BOM dropped)."""
        from taxjson.bin import taxjson_fetch as F
        from taxjson.lib.brokerages.base import decode_broker_text
        text = ("Statement,Header,Field Name,Field Value\n"
                "Statement,Data,BrokerName,Interactive Brokers\n"
                "Trades,Header,DataDiscriminator,Asset Category\n")
        raw = b"\xef\xbb\xbf" + text.encode()
        self.assertTrue(F.looks_like_ib_statement(decode_broker_text(raw)))
        src = (REPO_ROOT / "src" / "taxjson" / "bin"
               / "taxjson_run.py").read_text()
        self.assertNotIn('text = raw.decode("utf-8", "replace")', src)


if __name__ == "__main__":
    unittest.main()

"""Backlog fix round 4 of 2026-10-29, group `commands`: exit codes,
messages and child processes.

TJ-0632  every child Python is built by lib/dispatch.py
         python_module_argv: no `[sys.executable, "-m", ...]` in
         src/taxjson (a source scan); the tool commands taxjson_run
         hands to dispatch.run_cmd still run in process.
TJ-0630  sanity's usage errors (an item that is no account or file, a
         malformed pair, an account or file in two groups, the
         aggregate form without one of each) exit 2; 1 stays the
         verdict, positions differ.
TJ-0631  sanity names an unreadable gains file work/<file>, once: the
         Error that refuses its account says it all, with no warning
         before it; "no gains files in work/".
TJ-0633  a relayed child error (`tjs amt` / `tjs instalments` over a
         failed estimate) keeps no label of its own under the
         wrapper's one Error.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account names (margin), amounts under 1,000.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "taxjson"
_DISPATCH = "src/taxjson/lib/dispatch.py"


# ---- TJ-0632: no bare `[sys.executable, "-m", ...]` in the package -------
def _is_sys_executable(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "executable"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def python_launches(source, name="<source>"):
    """Each list / tuple display in SOURCE that starts a Python child
    other than through python_module_argv, as 'name:line': outside
    lib/dispatch.py any display that begins with sys.executable; in
    dispatch.py (which builds the -P and bootstrap forms) a bare
    `[sys.executable, "-m", ...]`."""
    out = []
    for node in ast.walk(ast.parse(source, name)):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        if not _is_sys_executable(node.elts[0]):
            continue
        if name == _DISPATCH:
            second = node.elts[1] if len(node.elts) > 1 else None
            if not (isinstance(second, ast.Constant) and second.value == "-m"):
                continue
        out.append(f"{name}:{node.lineno}")
    return out


class TestTJ0632EveryChildPythonThroughPythonModuleArgv(unittest.TestCase):
    """TJ-0632: `instalments`, `amt`, close-year's carry-forwards, the
    carryover ledger and `watch --harvest` built their child as
    `[sys.executable, "-m", "taxjson.bin.taxjson_run", ...]`, and so did
    every pipeline stage (`_cmd`): a list that imports a planted json.py
    from the folder it runs in, safe only while every one of them went
    through dispatch.run_cmd. Each is built by python_module_argv now,
    which run_cmd still runs in process."""

    def test_the_scan_sees_what_it_looks_for(self):
        self.assertEqual(python_launches(
            'import sys\nx = [sys.executable, "-m", "taxjson.bin.x"]\n'),
            ["<source>:2"])
        self.assertEqual(python_launches(
            'import sys\nx = (sys.executable, "-c", "print(1)")\n'),
            ["<source>:2"])
        self.assertEqual(python_launches(
            'import sys\nx = [sys.executable] + ["-m", "y"]\n'),
            ["<source>:2"])
        self.assertEqual(python_launches(
            'x = ["git", "-m", "msg"]\ny = sys.executable\n'), [])
        # dispatch.py builds the safe forms; a bare -m there is flagged.
        self.assertEqual(python_launches(
            'import sys\nx = [sys.executable, "-P", "-m", m]\n'
            'y = [sys.executable, "-c", BOOT, m]\n', _DISPATCH), [])
        self.assertEqual(python_launches(
            'import sys\nx = [sys.executable, "-m", m]\n', _DISPATCH),
            [f"{_DISPATCH}:2"])

    def test_no_bare_dash_m_in_the_package(self):
        bad = []
        for f in sorted(PKG.rglob("*.py")):
            rel = f.relative_to(REPO).as_posix()
            bad += python_launches(f.read_text(encoding="utf-8"), rel)
        self.assertEqual(bad, [], "build a child Python's argv with "
                         "taxjson.lib.dispatch.python_module_argv")

    def test_the_safe_forms_still_run_in_process(self):
        from taxjson.lib.dispatch import tool_module
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                cmd = python_module_argv("taxjson.bin.taxjson_sort",
                                         ["a.json"], legacy=legacy)
                self.assertEqual(tool_module(cmd),
                                 ("taxjson.bin.taxjson_sort", ["a.json"]))
                self.assertIsNone(tool_module(
                    python_module_argv("json.tool", legacy=legacy)))
        # The old marker still dispatches (a caller outside the package).
        self.assertEqual(
            tool_module([sys.executable, "-m", "taxjson.bin.taxjson_sort"]),
            ("taxjson.bin.taxjson_sort", []))

    def test_a_stage_command_is_the_safe_form(self):
        from taxjson.bin import taxjson_run as tr
        from taxjson.lib.dispatch import tool_module
        cmd = tr._cmd("taxjson-gains") + ["--country", "canada", "b.json"]
        self.assertEqual(cmd[:-3], python_module_argv("taxjson.bin."
                                                      "taxjson_gains"))
        self.assertEqual(tool_module(cmd),
                         ("taxjson.bin.taxjson_gains",
                          ["--country", "canada", "b.json"]))
        # The run's "stopped at" line still names the step and its input.
        self.assertEqual(tr._stage_description(
            tr._cmd("taxjson-fees") + ["--account", "margin",
                                       "/p/work/margin_base.json"]),
            tr._stage_description([sys.executable, "-m",
                                   "taxjson.bin.taxjson_fees", "--account",
                                   "margin", "/p/work/margin_base.json"]))

    def test_a_relayed_estimate_never_imports_from_the_folder(self):
        # `tjs amt` runs the estimate as a child; out of process
        # (TAXJSON_DISPATCH=subprocess) a json.py in the shell's folder
        # must not run in it.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'province = "ON"\n[accounts.margin]\ntype = "taxable"\n')
            shell = Path(td) / "shell"
            shell.mkdir()
            marker = shell / "PLANTED_RAN"
            (shell / "json.py").write_text(
                f"open({str(marker)!r}, 'a').write('json')\n")
            env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                       TAXJSON_DISPATCH="subprocess",
                       PYTHONPATH=str(REPO / "src"))
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "amt"]),
                cwd=shell, env=env, capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=600)
            self.assertNotEqual(r.returncode, 0, r.stdout)   # no books
            self.assertIn("could not compute the estimate", r.stderr)
            self.assertFalse(marker.exists())


# ---- TJ-0630: sanity's usage errors exit 2 -------------------------------
class TestTJ0630SanityUsageErrorsExit2(unittest.TestCase):
    """TJ-0630: `tjs sanity nosuch`, `tjs sanity margin` (no file) and the
    other refusals of the arguments exited 1, the status of the verdict
    "positions differ": a script read a typo as a difference. A usage
    error is exit 2 (docs/commands.md, Exit codes), as round 3 made the
    refusals of books it cannot read (TJ-0353)."""

    @classmethod
    def setUpClass(cls):
        from test_fix_1029_r3_commands import _run_project
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _run_project(
            Path(cls._td.name),
            extra='[accounts.other]\ntype = "taxable"\n',
            more_inputs={"other": "BUYSELL 2025-01-07 10:00:00 ZZR.TO 2 "
                                  "CAD 50 100 0\n"})
        cls.h = Path(cls._td.name) / "pos.toml"
        cls.h.write_text('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 1\n')

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def sanity(self, *args):
        from test_fix_1029_r3_commands import _tjs
        return _tjs(self.root, "sanity", *args)

    def test_each_usage_error_exits_2(self):
        h = str(self.h)
        for args, says in (
                (("nosuch",), "is neither an account"),
                (("margin",), "the aggregate form needs at least one "
                              "ACCOUNT and one FILE.toml"),
                ((h,), "the aggregate form needs at least one"),
                ((f"nosuch={h}",), "'nosuch' is not an account"),
                ((f"={h}",), "paired form is ACCOUNT[+ACCOUNT...]="),
                ((f"margin={h}", "margin"),
                 "account 'margin' appears in more than one group"),
                ((f"margin={h}", f"other={h}"),
                 "file pos.toml appears in more than one group")):
            with self.subTest(args=args):
                r = self.sanity(*args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(says, r.stderr)
                self.assertEqual(r.stdout, "")

    def test_a_difference_stays_exit_1_and_a_match_0(self):
        self.assertEqual(self.sanity(f"margin={self.h}").returncode, 0)
        r = self.sanity(f"margin+other={self.h}")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISSING_IN_HOLDINGS", r.stdout)

    def test_the_run_names_a_holdings_account_without_books(self):
        # The one refusal `tjs run`'s own check (no arguments) can meet:
        # an account taxjson.toml pairs with a holdings file has no
        # books. Exit 2 now: the run still names it and goes on. (Its
        # text says it has no inputs, not "not an account": TJ-0639.)
        from test_fix_1029_r3_commands import _tjs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.root, root)
            with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
                fh.write(f'[accounts.zempty]\ntype = "taxable"\n'
                         f'holdings = ["{self.h}"]\n')
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("taxjson.toml: account 'zempty' has no inputs",
                          r.stderr)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn("error: taxjson.toml: account 'zempty' has no "
                          "inputs", err)
            self.assertIn("warning: holdings check could not run", err)
            self.assertIn("The Error above names the file to fix", err)
            # As a person reads it: the Error, then one line naming the
            # command (the reason was "The Error above …" and the
            # command twice).
            from _qa_project import console, tj
            out = console(tj(root, "run", "--no-input"))
            self.assertIn("Error: taxjson.toml: account 'zempty' has no "
                          "inputs", out)
            self.assertIn("Warning: holdings check could not run — "
                          "`taxjson sanity`", out)
            self.assertNotIn("could not run: The Error above", out)


# ---- TJ-0631: an unreadable gains file named work/<file>, once ------------
class TestTJ0631SanityNamesTheGainsFileOnce(unittest.TestCase):
    """TJ-0631: with a damaged work/margin_gains_wash.json, `tjs sanity`
    warned "could not read /abs/…/work/margin_gains_wash.json: …", then
    its Error named the same file and reason again as work/…; "no gains
    files in /abs/…/work" named the folder by its absolute path too. The
    file is named from the project (docs/output-style.md) and said once:
    the Error alone when it refuses the account, the warning alone
    (naming what is left out) when the account is not in the check."""

    @classmethod
    def setUpClass(cls):
        from test_fix_1029_r3_commands import _run_project
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _run_project(
            Path(cls._td.name),
            extra='[accounts.other]\ntype = "taxable"\n',
            more_inputs={"other": "BUYSELL 2025-01-07 10:00:00 ZZR.TO 2 "
                                  "CAD 50 100 0\n"})
        cls.h = Path(cls._td.name) / "pos.toml"
        cls.h.write_text('[[holding]]\nsymbol = "ZZR.TO"\nquantity = 2\n')

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _broken(self, td):
        from test_fix_1029_r3_commands import _gains_file
        root = Path(td) / "p"
        shutil.copytree(self.root, root)
        g = _gains_file(root)
        g.write_text("{")
        return root, g.name

    def test_the_error_alone_when_it_refuses_the_account(self):
        from test_fix_1029_r3_commands import _tjs
        with tempfile.TemporaryDirectory() as td:
            root, name = self._broken(td)
            # No arguments (every account of taxjson.toml), the paired
            # form, and the account named bare.
            for args in ((), (f"margin={self.h}",), ("margin", str(self.h))):
                with self.subTest(args=args):
                    r = _tjs(root, "sanity", *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    err = " ".join(r.stderr.split())
                    self.assertIn(f"error: work/{name}: ", err)
                    self.assertIn("the books of 'margin' cannot be read",
                                  err)
                    self.assertEqual(err.count(f"work/{name}"), 1, err)
                    self.assertNotIn("warning: could not read", err)
                    self.assertNotIn("is neither an account", err)
                    self.assertNotIn(str(root), err)

    def test_the_warning_alone_when_the_account_is_left_out(self):
        from test_fix_1029_r3_commands import _tjs
        with tempfile.TemporaryDirectory() as td:
            root, name = self._broken(td)
            r = _tjs(root, "sanity", f"other={self.h}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn(f"warning: could not read work/{name} — 'margin' "
                          f"is not in this check", err)
            self.assertIn("Expecting property name", err)
            self.assertEqual(err.count(f"work/{name}"), 1, err)
            self.assertNotIn(str(root), err)
            # As a person reads it: the file and what is left out on the
            # one line, before the width cuts it.
            from _qa_project import console, tj
            out = console(tj(root, "sanity", f"other={self.h}"))
            self.assertIn(f"Warning: could not read work/{name} — 'margin' "
                          f"is not in this check", out)

    def test_no_gains_files_names_work(self):
        from test_fix_1029_r3_commands import _tjs
        with tempfile.TemporaryDirectory() as td:
            root, _name = self._broken(td)
            for p in (root / "work").glob("*gains*.json"):
                p.unlink()
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn("no gains files in work/", err)
            self.assertIn("run `taxjson run`", err)
            self.assertNotIn(str(root), err)


# ---- TJ-0633: one label per relayed error --------------------------------
class TestTJ0633RelayedChildErrorHasOneLabel(unittest.TestCase):
    """TJ-0633: `tjs amt` and `tjs instalments` relay a failed estimate
    under their own "Error: could not compute the estimate it builds
    on". The kept line still began `taxjson estimate: error:` (captured)
    and showed as a second "Error: taxjson estimate: …" on a terminal:
    _child_error dropped only the person's form of the label."""

    def test_both_forms_of_the_label_are_dropped(self):
        from taxjson.bin.taxjson_run import _child_error
        for label in ("taxjson estimate: error: ", "Error: taxjson "
                      "estimate: ", "taxjson-merge: error: ", "error: ",
                      "Error: ", "ERROR: "):
            with self.subTest(label=label):
                msg = _child_error(
                    f"{label}could not read work/margin_gains.json: not "
                    f"valid JSON\n  Re-run `taxjson run` to rebuild it.\n")
                self.assertTrue(msg.startswith("could not read work/"), msg)
                self.assertNotIn("error:", msg.lower())
                self.assertIn("Re-run `taxjson run`", msg)
        # Over the limit: the error line found, its label dropped too.
        long = _child_error("Info: a note\ntaxjson estimate: error: "
                            "could not read " + "x" * 500 + "\n  Re-run.\n")
        self.assertTrue(long.startswith("could not read"), long[:80])
        # A line that only mentions an error is not a label.
        self.assertEqual(_child_error("ValueError: bad\n"), "ValueError: bad")
        self.assertEqual(_child_error("no error: here\n"), "no error: here")

    def test_amt_and_instalments_say_error_once(self):
        from test_fix_1029_r3_commands import _gains_file, _run_project, _tjs
        from _qa_project import console, tj
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td))
            with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
                fh.write('[instalments]\nbasis = "current_year"\n'
                         'withheld = 0\n')
            text = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(text.replace(
                'source_currencies = []\n',
                'source_currencies = []\nprovince = "ON"\n', 1))
            _gains_file(root).write_text("{")
            for cmd in ("amt", "instalments"):
                with self.subTest(cmd=cmd):
                    r = _tjs(root, cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    err = r.stderr
                    self.assertIn(f"taxjson {cmd}: error: could not compute "
                                  f"the estimate it builds on", err)
                    self.assertIn("could not read work/", err)
                    self.assertEqual(err.lower().count("error:"), 1, err)
                    out = console(tj(root, cmd, check=False))
                    self.assertIn("Error: could not compute the estimate "
                                  "it builds on", out)
                    self.assertEqual(out.count("Error:"), 1, out)
                    self.assertNotIn("taxjson estimate:", out)


if __name__ == "__main__":
    unittest.main()

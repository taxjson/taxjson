"""A faster gate: the suite in parallel, and release.sh reusing a PASS.

scripts/run_tests_parallel.py runs each test module in a process of its
own (its own TMPDIR and synthetic HOME), longest first, and fails unless
every process passed and the tests run add up to the tests collected.
scripts/gate-record.sh records a full PASS of a clean tree by its tree
hash; scripts/release.sh skips the gate when the tree it releases has
one, after proving its own edits touch only version, tag and date lines.
Every repository here is a temporary one; nothing is pushed anywhere
but a local bare repository.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (the synthetic HOME first)
from _support.release_repo import _ReleaseRepo, _PY_STUB
import _paths

REPO = _paths.REPO
RUNNER = REPO / "scripts" / "run_tests_parallel.py"
_BASH = shutil.which("bash")
_GIT = shutil.which("git")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_tests_parallel", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load_runner()

# A module that proves it has a TMPDIR and HOME of its own: it writes
# its name to a FIXED file name in the temp folder, waits, and reads it
# back — two modules sharing a temp folder would read each other's.
_COLLIDER = """
import os, tempfile, time, unittest
NAME = __name__
class TestShared(unittest.TestCase):
    def test_fixed_temp_name(self):
        p = os.path.join(tempfile.gettempdir(), "shared.txt")
        with open(p, "w") as f:
            f.write(NAME)
        time.sleep(0.4)
        with open(p) as f:
            self.assertEqual(f.read(), NAME)
    def test_home_is_private(self):
        home = os.environ["HOME"]
        self.assertTrue(home.startswith(tempfile.gettempdir()), home)
        p = os.path.join(home, "mine")
        self.assertFalse(os.path.exists(p))
        open(p, "w").close()
"""


class TestPlan(unittest.TestCase):
    def test_discover_modules_follows_packages(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "test_a.py").write_text("")
            (d / "helper.py").write_text("")
            (d / "pkg").mkdir()
            (d / "pkg" / "__init__.py").write_text("")
            (d / "pkg" / "test_b.py").write_text("")
            (d / "nopkg").mkdir()
            (d / "nopkg" / "test_c.py").write_text("")
            self.assertEqual(R.discover_modules(d, "test_*.py"),
                             ["pkg", "pkg.test_b", "test_a"])

    def test_longest_first_and_split(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for n, size in (("test_small", 10), ("test_big", 40000),
                            ("test_slow", 10), ("test_empty", 10)):
                (d / f"{n}.py").write_text("x" * size)
            collected = {
                "test_small": {"count": 2, "classes": {"A": 2}},
                "test_big": {"count": 3, "classes": {"A": 3}},
                "test_slow": {"count": 4, "classes": {"A": 1, "B": 3}},
                "test_empty": {"count": 0, "classes": {}},
            }
            # No recorded times: by file size; nothing split.
            parts = R.plan(collected, d, {}, 25.0)
            self.assertEqual([p[0] for p in parts],
                             ["test_big", "test_slow", "test_small"])
            # test_slow took 60 s last time: a part per class, its
            # share of the time each, and still longest first.
            parts = R.plan(collected, d, {"test_slow": 60.0,
                                          "test_slow.B": 50.0}, 25.0)
            self.assertEqual([(p[0], p[2]) for p in parts],
                             [("test_slow.B", 3), ("test_slow.A", 1),
                              ("test_big", 3), ("test_small", 2)])
            self.assertEqual(sum(p[2] for p in parts), 9)
            # On 2 processes the run is long enough to absorb 60 s:
            # half a process's share (60 s) is not exceeded, no split.
            many = dict(collected, **{
                f"test_m{i}": {"count": 1, "classes": {"A": 1}}
                for i in range(3)})
            for i in range(3):
                (d / f"test_m{i}.py").write_text("")
            times = {"test_slow": 60.0, "test_big": 60.0, "test_small": 0.0,
                     "test_m0": 60.0, "test_m1": 60.0, "test_m2": 0.0}
            parts = R.plan(many, d, times, 25.0, jobs=2)
            self.assertNotIn("test_slow.B", [p[0] for p in parts])
            parts = R.plan(many, d, times, 25.0, jobs=8)
            self.assertIn("test_slow.B", [p[0] for p in parts])

    def test_parse_result_takes_the_last_lines(self):
        out = ("a test printed: Ran 99 tests in 1.0s\n.F\n"
               "----------------------------------------------------------------------\n"
               "Ran 2 tests in 0.012s\n\nFAILED (failures=1)\n")
        self.assertEqual(R.parse_result(out), (2, "FAILED (failures=1)"))
        self.assertEqual(R.parse_result("Ran 1 test in 0.000s\n\nOK\n"),
                         (1, "OK"))
        self.assertEqual(R.parse_result("Segmentation fault\n"), (None, ""))

    def test_a_parts_skips_are_read_from_its_last_status_line(self):
        self.assertEqual(R.skipped_of("Ran 4 tests in 0.1s\n\nOK (skipped=3)\n"), 3)
        self.assertEqual(R.skipped_of(
            "FAILED (failures=1, skipped=2)\nRan 2 tests in 0.1s\n\nOK\n"), 0)
        self.assertEqual(R.skipped_of("Ran 1 test in 0.1s\n\nFAILED (errors=1, skipped=1)\n"), 1)
        self.assertEqual(R.skipped_of("Segmentation fault\n"), 0)

    def test_a_parts_output_is_unbuffered(self):
        # So what a test prints lands before unittest's summary (TJ-3729).
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(R.child_env(Path(d))["PYTHONUNBUFFERED"], "1")

    def test_a_status_line_a_test_printed_after_the_summary_is_not_read(self):
        # A part's stdout is buffered: what a test printed lands after
        # unittest's stderr summary (TJ-3712).
        out = "Ran 4 tests in 0.1s\n\nOK (skipped=1)\nOK (skipped=99)\nFAILED (errors=5)\n"
        self.assertEqual(R.parse_result(out), (4, "OK (skipped=1)"))
        self.assertEqual(R.skipped_of(out), 1)


class TestRunner(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.suite = self.d / "suite"
        self.suite.mkdir()
        self.durations = self.d / "durations.json"

    def tearDown(self):
        self._td.cleanup()

    def write(self, name, text):
        (self.suite / f"{name}.py").write_text(textwrap.dedent(text))

    def run_it(self, *args):
        env = {k: v for k, v in os.environ.items()
               if k != "TAXJSON_TEST_HOME"}
        return subprocess.run(
            [sys.executable, str(RUNNER), "-s", str(self.suite),
             "--durations", str(self.durations), *args],
            capture_output=True, text=True, env=env, cwd=self.d,
            stdin=subprocess.DEVNULL, timeout=300)

    def test_parts_have_their_own_temp_and_home(self):
        for n in ("test_one", "test_two", "test_three"):
            self.write(n, _COLLIDER)
        r = self.run_it("-j", "3", "-v")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 6 of 6 tests", r.stdout)
        self.assertIn("slowest", r.stdout)
        rec = json.loads(self.durations.read_text())["parts"]
        self.assertEqual(set(rec), {"test_one", "test_two", "test_three"})

    def test_failures_are_shown_in_full_and_fail_the_run(self):
        self.write("test_ok", """
            import unittest
            class T(unittest.TestCase):
                def test_fine(self): pass
            """)
        self.write("test_bad", """
            import unittest
            class T(unittest.TestCase):
                def test_wrong(self):
                    self.assertEqual(1, 2, "synthetic failure marker")
            """)
        self.write("test_setup_crash", """
            import unittest
            def setUpModule():
                raise RuntimeError("module setup broke")
            class T(unittest.TestCase):
                def test_never(self): pass
            """)
        r = self.run_it("-j", "2")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FAILED: test_bad", r.stdout)
        self.assertIn("synthetic failure marker", r.stdout)
        self.assertIn("FAILED: test_setup_crash", r.stdout)
        self.assertIn("module setup broke", r.stdout)
        self.assertNotIn("FAILED: test_ok", r.stdout)
        self.assertEqual(r.stdout.rstrip().splitlines()[-1], "FAILED")
        # Only the passing module's time is recorded.
        rec = json.loads(self.durations.read_text())["parts"]
        self.assertEqual(set(rec), {"test_ok"})

    def test_a_class_named_runs_alone_and_every_class_named_runs_the_module(self):
        # The no-extras stage names a split module's classes
        # (`module.Class`; scripts/ci_no_extras.sh EXTRAS_TESTS).
        self.write("test_two_kinds", """
            import unittest
            class TestA(unittest.TestCase):
                def test_a1(self): pass
                def test_a2(self): pass
            class TestB(unittest.TestCase):
                def test_b(self): pass
            """)
        r = self.run_it("-j", "1", "test_two_kinds.TestA")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 2 of 2 tests", r.stdout)
        r = self.run_it("-j", "1", "test_two_kinds.TestA", "test_two_kinds.TestB")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 3 of 3 tests", r.stdout)
        self.assertIn("1 processes", r.stdout)
        r = self.run_it("-j", "1", "test_two_kinds.TestC")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not test modules or classes here: test_two_kinds.TestC",
                      r.stdout)

    def test_select_reads_a_module_its_classes_and_nothing_else(self):
        collected = {"test_m": {"count": 3, "classes": {"TestA": 2, "TestB": 1}},
                     "pkg.test_p": {"count": 1, "classes": {"TestP": 1}},
                     "test_lt": {"count": 2, "classes": {}}}
        out, unknown = R.select(collected, ["test_m.TestB", "pkg.test_p",
                                            "test_lt.TestX", "test_none"])
        self.assertEqual(unknown, ["test_lt.TestX", "test_none"])
        self.assertEqual(out["test_m"], {"count": 1, "classes": {"TestB": 1},
                                         "only": True})
        self.assertIs(out["pkg.test_p"], collected["pkg.test_p"])
        # A module named whole wins over its classes, in either order.
        out, _ = R.select(collected, ["test_m.TestA", "test_m"])
        self.assertIs(out["test_m"], collected["test_m"])
        out, _ = R.select(collected, ["test_m", "test_m.TestA"])
        self.assertIs(out["test_m"], collected["test_m"])
        parts = R.plan(out, Path("."), {}, 1e9)
        self.assertEqual([p[0] for p in parts], ["test_m"])
        out, _ = R.select(collected, ["test_m.TestA"])
        parts = R.plan(out, Path("."), {}, 1e9)
        self.assertEqual([(p[0], p[2]) for p in parts], [("test_m.TestA", 2)])

    def test_a_process_that_exits_0_short_of_its_tests_fails(self):
        # Exit status 0, but the module never reported its tests: the
        # count check catches what an exit code alone would not.
        self.write("test_quits", """
            import os, unittest
            class T(unittest.TestCase):
                def test_a(self): os._exit(0)
                def test_b(self): pass
            """)
        r = self.run_it("-j", "1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ran None tests, holds 2", r.stdout)

    def test_a_module_that_does_not_import_fails_the_collect(self):
        self.write("test_broken", "import no_such_module_anywhere\n")
        self.write("test_ok", """
            import unittest
            class T(unittest.TestCase):
                def test_fine(self): pass
            """)
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("COLLECT FAILED", r.stdout)
        self.assertIn("test_broken", r.stdout)

    def test_a_bound_test_class_fails_the_collect(self):
        # A class bound in a second module runs there too (TJ-3759,
        # TJ-3772): the collect names the module that binds it, whatever
        # the form, and a class whose tests are inherited too.
        self.write("test_def", """
            import unittest
            class _Mixin:
                def test_m(self): pass
            class TestFoo(unittest.TestCase):
                def test_a(self): pass
            class TestBar(_Mixin, unittest.TestCase):
                pass
            """)
        self.write("test_use", """
            import test_def
            Borrowed = test_def.TestFoo
            from test_def import TestBar
            """)
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("COLLECT FAILED — 2 TestCase class(es) with tests bound where they "
                      "are not defined", r.stdout)
        self.assertIn("  test_use.Borrowed: test_def.TestFoo\n", r.stdout)
        self.assertIn("  test_use.TestBar: test_def.TestBar\n", r.stdout)

    def test_an_alias_or_a_load_tests_module_fails_the_collect(self):
        # TJ-3779: a second name in the defining module, and a module with
        # load_tests, bind a class too.
        self.write("test_alias", """
            import unittest
            class TestFoo(unittest.TestCase):
                def test_a(self): pass
            TestOld = TestFoo
            """)
        self.write("test_lt", """
            import unittest
            from test_other import TestO
            def load_tests(loader, tests, pattern):
                return tests
            """)
        self.write("test_other", """
            import unittest
            class TestO(unittest.TestCase):
                def test_o(self): pass
            """)
        # And a helper's class, though one module binds it (TJ-3759).
        self.write("_held", """
            import unittest
            class TestH(unittest.TestCase):
                def test_h(self): pass
            """)
        self.write("test_hu", "from _held import TestH\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("  test_hu.TestH: _held.TestH\n", r.stdout)
        self.assertIn("  test_alias.TestOld: test_alias.TestFoo\n", r.stdout)
        self.assertIn("  test_lt.TestO: test_other.TestO\n", r.stdout)

    def test_a_load_tests_that_loads_another_module_fails_the_collect(self):
        # TJ-3784: the collect reads what a module's suite runs.
        self.write("test_other", """
            import unittest
            class TestO(unittest.TestCase):
                def test_o(self): pass
            """)
        self.write("test_lt", """
            def load_tests(loader, tests, pattern):
                tests.addTests(loader.loadTestsFromName("test_other"))
                return tests
            """)
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("  test_lt.(load_tests): test_other.TestO\n", r.stdout)

    def collect(self, files):
        """The runner over a suite of FILES alone: (its exit code, its output)."""
        shutil.rmtree(self.suite)
        self.suite.mkdir()
        for name, text in files.items():
            (self.suite / name).write_text(text)
        r = self.run_it()
        return r.returncode, r.stdout + r.stderr

    def test_a_load_tests_of_doctests_or_a_helpers_cases(self):
        # TJ-3802, TJ-3803: a helper's class load_tests builds cases from
        # (a doctest's too) fails only on the same ids from two modules; a
        # helper named unittest_* is no library's.
        doc = ('"""D."""\nimport doctest\n\n\ndef f():\n    """\n    >>> 1\n    1\n    """\n\n\n'
               'def load_tests(loader, tests, pattern):\n'
               '    tests.addTests(doctest.DocTestSuite(__name__))\n    return tests\n')
        param = ('import unittest\n\n\nclass ParamCase(unittest.TestCase):\n'
                 '    def __init__(self, name, p):\n        super().__init__(name)\n'
                 '        self.p = p\n\n    def id(self):\n'
                 '        return f"{super().id()}[{self.p}]"\n\n    def test_p(self):\n'
                 '        pass\n')
        def user(p):
            return ('import _param\n\n\ndef load_tests(loader, tests, pattern):\n'
                    f'    tests.addTest(_param.ParamCase("test_p", {p!r}))\n    return tests\n')
        extra = ('import unittest\n\n\nclass TestE(unittest.TestCase):\n'
                 '    def test_e(self):\n        pass\n')
        cases = (
            ("a doctest", {"test_doc.py": doc}, None),
            ("a doctest twice", {"test_doc.py": doc, "test_doc2.py": doc.replace(
                "DocTestSuite(__name__)", "DocTestSuite('test_doc')")},
             "test_doc.(load_tests), test_doc2.(load_tests): doctest.DocTestCase "
             "(the same test ids)"),
            ("a helper's cases, two ids", {"_param.py": param, "test_pa.py": user("a"),
                                           "test_pb.py": user("b")}, None),
            ("a helper's cases, one id", {"_param.py": param, "test_pa.py": user("a"),
                                          "test_pb.py": user("a")},
             "test_pa.(load_tests), test_pb.(load_tests): _param.ParamCase (the same test ids)"),
            ("a unittest_* helper", {"unittest_extra.py": extra,
                                     "test_ua.py": "from unittest_extra import TestE\n",
                                     "test_ub.py": "from unittest_extra import TestE\n"},
             "test_ua.TestE, test_ub.TestE: unittest_extra.TestE"),
        )
        for why, files, bad in cases:
            with self.subTest(why):
                rc, out = self.collect(files)
                if bad is None:
                    self.assertEqual(rc, 0, out)
                else:
                    self.assertEqual(rc, 1, out)
                    self.assertIn(f"  {bad}\n", out)

    def test_a_class_its_home_holds_another_object_for(self):
        # TJ-3809: at home means the home module holds this class under its
        # name, not another object of that name.
        home = ('import unittest\n\n\ndef make(n):\n    class Case(unittest.TestCase):\n'
                '        def test_x(self):\n            pass\n    Case.n = n\n    return Case\n\n\n'
                'Case = make(1)\n')
        rc, out = self.collect({"test_home.py": home,
                                "test_user.py": "from test_home import make\n\nBuilt = make(2)\n"})
        self.assertEqual(rc, 0, out)

    def test_a_load_tests_that_drops_a_bound_class_passes(self):
        self.write("test_other", """
            import unittest
            class TestO(unittest.TestCase):
                def test_o(self): pass
            """)
        self.write("test_lt", """
            import unittest
            from test_other import TestO
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite()
            """)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_class_a_factory_builds_runs_once(self):
        # TJ-3780: a helper's factory (or a decorator that subclasses)
        # makes a class no module holds at its top: it runs once.
        self.write("_factory", """
            import unittest
            def __getattr__(name):           # a lazy loader's (TJ-3786)
                if name == "Case":
                    raise ImportError(name)
                raise AttributeError(name)
            def make(value):
                class Case(unittest.TestCase):
                    def test_v(self):
                        self.assertTrue(value)
                return Case
            """)
        for name in ("test_f1", "test_f2"):
            self.write(name, """
                import _factory
                TestMade = _factory.make(1)
                """)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 2 of 2 tests", r.stdout)
        # One made class that two modules bind runs in both.
        self.write("_made", "import _factory\nShared = _factory.make(1)\n")
        for name in ("test_f1", "test_f2"):
            self.write(name, "from _made import Shared\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("  test_f1.Shared, test_f2.Shared: _factory.make.<locals>.Case\n", r.stdout)

    def test_bound_classes_past_twenty_are_counted(self):
        body = "import unittest\n" + "".join(
            f"class TestC{i}(unittest.TestCase):\n    def test_a(self): pass\n" for i in range(22))
        self.write("test_many", body)
        self.write("test_borrow", "from test_many import *\n")
        r = self.run_it()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("COLLECT FAILED — 22 TestCase class(es)", r.stdout)
        self.assertIn("  ... and 2 more\n", r.stdout)

    def test_ids_alike_are_no_bound_class(self):
        # FunctionTestCase ids are the function's name, and a load_tests
        # may run one case twice: neither is a class bound twice.
        for name in ("test_lt1", "test_lt2"):
            self.write(name, """
                import unittest
                def load_tests(loader, tests, pattern):
                    s = unittest.TestSuite()
                    s.addTest(unittest.FunctionTestCase(lambda: None))
                    s.addTest(unittest.FunctionTestCase(lambda: None))
                    return s
                """)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_named_modules_and_serial_fallback(self):
        self.write("test_a", """
            import unittest
            class T(unittest.TestCase):
                def test_1(self): pass
                def test_2(self): pass
            """)
        self.write("test_b", """
            import unittest
            class T(unittest.TestCase):
                def test_1(self): self.fail("not selected")
            """)
        r = self.run_it("test_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 2 of 2 tests", r.stdout)
        r = self.run_it("--serial")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Ran 3 tests", r.stderr)
        r = self.run_it("test_nope")
        self.assertEqual(r.returncode, 2)

    def test_a_split_module_runs_every_class(self):
        self.write("test_two_classes", """
            import unittest
            class A(unittest.TestCase):
                def test_1(self): pass
            class B(unittest.TestCase):
                def test_1(self): pass
                def test_2(self): pass
            """)
        self.durations.write_text(json.dumps(
            {"parts": {"test_two_classes": 100.0}}))
        r = self.run_it("-v")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("test_two_classes.A (1/1)", r.stdout)
        self.assertIn("test_two_classes.B (2/2)", r.stdout)
        self.assertIn("Ran 3 of 3 tests in", r.stdout)

    def test_the_summary_sums_the_parts_skips(self):
        self.write("test_skips", """
            import unittest
            class A(unittest.TestCase):
                @unittest.skip("demo")
                def test_1(self): pass
                def test_2(self): pass
            class B(unittest.TestCase):
                @unittest.skip("demo")
                def test_1(self): pass
            """)
        self.durations.write_text(json.dumps({"parts": {"test_skips": 100.0}}))
        r = self.run_it("-v")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Ran 3 of 3 tests (2 skipped) in", r.stdout)


class TestGateWiring(unittest.TestCase):
    def test_ci_runs_the_suite_in_parallel_with_a_serial_fallback(self):
        ci = (REPO / "scripts" / "ci.sh").read_text()
        self.assertIn('"$PY" scripts/run_tests_parallel.py', ci)
        self.assertIn("--serial) SERIAL=--serial", ci)
        self.assertIn("SUITE_ARGS=(--serial)", ci)
        self.assertIn("bash scripts/gate-record.sh write", ci)
        self.assertIn("--release-edits) MODE=release-edits", ci)

    def test_durations_cache_is_gitignored(self):
        r = subprocess.run(["git", "check-ignore", "-q",
                            ".ci/test-durations.json"], cwd=REPO)
        if r.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(r.returncode, 0)

    def test_the_real_suite_preloads_hermetic(self):
        self.assertTrue((REPO / "tests" / R.PRELOAD / "__init__.py").is_file())


def _git(cwd, *args, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, env=env)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@unittest.skipUnless(_BASH and _GIT, "bash and git required")
class TestGateRecord(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.repo = self.d / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "gate-record.sh",
                    self.repo / "scripts")
        self.cache = self.d / "records"
        self.env = dict(os.environ, TAXJSON_GATE_CACHE=str(self.cache),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Sam Synthetic",
                        GIT_AUTHOR_EMAIL="sam@example.com",
                        GIT_COMMITTER_NAME="Sam Synthetic",
                        GIT_COMMITTER_EMAIL="sam@example.com")
        _git(self.d, "init", "-q", str(self.repo), env=self.env)
        (self.repo / "a.txt").write_text("a\n")
        _git(self.repo, "add", "-A", env=self.env)
        _git(self.repo, "commit", "-q", "-m", "start", env=self.env)
        self.tree = _git(self.repo, "rev-parse", "HEAD^{tree}", env=self.env)

    def tearDown(self):
        self._td.cleanup()

    def gr(self, *args):
        return subprocess.run(["bash", "scripts/gate-record.sh", *args],
                              cwd=self.repo, capture_output=True, text=True,
                              env=self.env)

    def test_written_only_for_a_clean_full_gate(self):
        r = self.gr("write", self.tree, "quick", "3.12.3", "5")
        self.assertIn("not written (mode quick", r.stdout)
        (self.repo / "b.txt").write_text("untracked\n")
        r = self.gr("write", self.tree, "default", "3.12.3", "5")
        self.assertIn("not written (the working tree is not clean)", r.stdout)
        (self.repo / "b.txt").unlink()
        (self.repo / "a.txt").write_text("modified\n")
        r = self.gr("write", self.tree, "default", "3.12.3", "5")
        self.assertIn("not written (the working tree is not clean)", r.stdout)
        _git(self.repo, "checkout", "--", "a.txt", env=self.env)
        r = self.gr("write", "f" * 40, "default", "3.12.3", "5")
        self.assertIn("not written (HEAD moved", r.stdout)
        self.assertFalse(self.cache.exists() and any(self.cache.iterdir()))
        r = self.gr("write", self.tree, "default", "3.12.3", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        f = self.cache / f"{self.tree}.default.py3.12.3.pass"
        self.assertTrue(f.is_file(), r.stdout)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        text = f.read_text()
        self.assertIn(f"tree={self.tree}\n", text)
        self.assertIn("mode=default\n", text)

    def test_find_matches_tree_python_and_age(self):
        self.assertEqual(self.gr("find", self.tree, "3.12.3", "7").returncode, 1)
        self.gr("write", self.tree, "default", "3.12.3", "5")
        r = self.gr("find", self.tree, "3.12.3", "7")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip().endswith(".default.py3.12.3.pass"))
        self.assertEqual(self.gr("find", self.tree, "3.11.9", "7").returncode, 1)
        self.assertEqual(self.gr("find", "e" * 40, "3.12.3", "7").returncode, 1)
        # Eight days old: too old for 7, fine for 10.
        f = Path(r.stdout.strip())
        old = int(time.time()) - 8 * 86400
        f.write_text("\n".join(
            f"passed_at={old}" if ln.startswith("passed_at=") else ln
            for ln in f.read_text().splitlines()) + "\n")
        self.assertEqual(self.gr("find", self.tree, "3.12.3", "7").returncode, 1)
        self.assertEqual(self.gr("find", self.tree, "3.12.3", "10").returncode, 0)
        # A nightly PASS counts as a full gate.
        self.gr("write", self.tree, "nightly", "3.12.3", "9")
        r = self.gr("find", self.tree, "3.12.3", "7")
        self.assertTrue(r.stdout.strip().endswith(".nightly.py3.12.3.pass"))


# The python stand-in, plus the version release.sh asks for; with
# PIP_TOUCH set, `pip install` also changes that file (something besides
# the release edits changing the tree).
_PY_STUB_V = _PY_STUB.replace(
    'if [ "$1 $2" = "-m pip" ]; then exit 0; fi',
    'if [ "$1 $2" = "-m pip" ]; then [ -z "${PIP_TOUCH:-}" ] || '
    'echo changed >> "$PIP_TOUCH"; exit 0; fi\n'
    'if [ "$1" = "-c" ]; then echo 3.12.3; exit 0; fi')


class TestReleaseReusesAPass(_ReleaseRepo):
    def setUp(self):
        super().setUp()
        s = self.dev / "scripts"
        shutil.copy(REPO / "scripts" / "gate-record.sh", s)
        self.ci_log = self.d / "ci.log"
        self._stub("ci.sh", "#!/usr/bin/env bash\n"
                   f"echo \"ci $*\" >> '{self.ci_log}'\necho '== ci: PASS =='\n",
                   where=s)
        (self.dev / "docs" / "troubleshooting.md").write_text(
            "# Troubleshooting\n\n## A problem\n\n- **Fixed in:** unreleased\n"
            "- **Fixed in:** `v0.1.0`\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "gate record helper")
        self.git(self.dev, "push", "-q", "origin", "main")
        self.py = self._stub("python-stub", _PY_STUB_V, where=self.d)
        self.env["PYTHON"] = str(self.py)
        self.env["TAXJSON_GATE_CACHE"] = str(self.d / "records")
        self.tree = self.git(self.dev, "rev-parse", "HEAD^{tree}")

    def record(self, py="3.12.3"):
        r = subprocess.run(["bash", "scripts/gate-record.sh", "write",
                            self.tree, "default", py, "60"], cwd=self.dev,
                           capture_output=True, text=True, env=self.env)
        self.assertIn("gate record:", r.stdout)
        return Path(r.stdout.split("gate record: ", 1)[1].strip())

    def ci_calls(self):
        return (self.ci_log.read_text().splitlines()
                if self.ci_log.exists() else [])

    def test_a_pass_of_the_tree_is_reused(self):
        rec = self.record()
        r = self.release("v0.2.0", "--prepare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_calls(), ["ci --release-edits"])
        self.assertIn(f"reusing the PASS of tree {self.tree[:12]}", r.stdout)
        self.assertIn(str(rec), r.stdout)
        self.assertIn('version = "0.2.0"', self.origin_version())
        # The version commit's tree is the gated tree plus the release
        # edits only.
        changed = self.git(self.dev, "diff", "--name-only", self.tree,
                           "origin/main")
        self.assertEqual(sorted(changed.split()), [
            "CHANGELOG.md", "docs/troubleshooting.md",
            "packages/taxjson-fetch/pyproject.toml", "pyproject.toml"])

    def test_the_upgrade_notes_heading_is_a_release_edit(self):
        (self.dev / "docs" / "upgrading.md").write_text(
            "### Unreleased\n\n- New note.\n\n### v0.1.0\n\n- Old.\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "notes")
        self.git(self.dev, "push", "-q", "origin", "main")
        self.tree = self.git(self.dev, "rev-parse", "HEAD^{tree}")
        self.record()
        r = self.release("v0.2.0", "--prepare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_calls(), ["ci --release-edits"])
        self.assertIn("docs/upgrading.md", self.git(
            self.dev, "diff", "--name-only", self.tree, "origin/main"))

    def test_fresh_gate_and_no_record_run_the_full_gate(self):
        self.record()
        r = self.release("v0.2.0", "--prepare", "--fresh-gate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_calls(), ["ci "])
        self.assertIn("== full gate ==", r.stdout)

    def test_another_python_or_an_old_record_is_not_reused(self):
        self.record(py="3.11.9")
        r = self.release("v0.2.0", "--prepare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_calls(), ["ci "])

    def test_old_record_needs_a_longer_max_age(self):
        rec = self.record()
        old = int(time.time()) - 9 * 86400
        rec.write_text("\n".join(
            f"passed_at={old}" if ln.startswith("passed_at=") else ln
            for ln in rec.read_text().splitlines()) + "\n")
        r = self.release("v0.2.0", "--prepare", "--gate-max-age", "10")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.ci_calls(), ["ci --release-edits"])

    def test_a_change_beyond_the_release_edits_runs_the_gate(self):
        self.record()
        self.env["PIP_TOUCH"] = str(self.dev / "pyproject.toml")
        r = self.release("v0.2.0", "--prepare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pyproject.toml differs from HEAD beyond the release edits",
                      r.stdout)
        self.assertIn("not reusing", r.stdout)
        self.assertEqual(self.ci_calls(), ["ci "])

    def test_an_untracked_file_runs_the_gate(self):
        self.record()
        self.env["PIP_TOUCH"] = str(self.dev / "docs" / "new.md")
        r = self.release("v0.2.0", "--prepare")
        self.assertIn("docs/new.md is changed besides the release edits",
                      r.stdout)
        self.assertEqual(self.ci_calls(), ["ci "])

    def test_bad_max_age_is_refused(self):
        r = self.release("v0.2.0", "--prepare", "--gate-max-age", "soon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--gate-max-age must be a whole number", r.stdout)


if __name__ == "__main__":
    unittest.main()

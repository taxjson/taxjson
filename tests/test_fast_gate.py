"""A faster gate: the suite in parallel.

scripts/run_tests_parallel.py runs each test module in a process of its
own (its own TMPDIR and synthetic HOME), longest first, and fails unless
every process passed and the tests run add up to the tests collected.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (the synthetic HOME first)

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "scripts" / "run_tests_parallel.py"


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
        self.assertIn("Ran 3 of 3 tests", r.stdout)


class TestGateWiring(unittest.TestCase):
    def test_ci_runs_the_suite_in_parallel_with_a_serial_fallback(self):
        ci = (REPO / "scripts" / "ci.sh").read_text()
        self.assertIn('"$PY" scripts/run_tests_parallel.py', ci)
        self.assertIn("--serial) SERIAL=--serial", ci)
        self.assertIn("SUITE_ARGS=(--serial)", ci)

    def test_durations_cache_is_gitignored(self):
        r = subprocess.run(["git", "check-ignore", "-q",
                            ".ci/test-durations.json"], cwd=REPO)
        if r.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(r.returncode, 0)

    def test_the_real_suite_preloads_hermetic(self):
        self.assertTrue((REPO / "tests" / R.PRELOAD / "__init__.py").is_file())


if __name__ == "__main__":
    unittest.main()

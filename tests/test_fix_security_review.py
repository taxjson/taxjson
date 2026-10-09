"""2026-10 security review pins (synthetic data only)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
ENV = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")


def _plant(folder: Path) -> Path:
    """A json.py in `folder` that leaves a marker file when imported."""
    marker = folder / "PLANTED_RAN"
    for mod in ("json", "csv", "runpy"):
        (folder / f"{mod}.py").write_text(
            f"open({str(marker)!r}, 'a').write({mod!r})\n")
    return marker


class TestChildPythonNeverImportsFromCwd(unittest.TestCase):
    """H1: a json.py / csv.py planted in a project folder never runs in a
    taxjson child Python process."""

    def test_plain_dash_m_does_import_it(self):
        # The control: what the old launch shape did.
        with tempfile.TemporaryDirectory() as tmp:
            marker = _plant(Path(tmp))
            subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_sort",
                            "--help"], cwd=tmp, env=ENV,
                           capture_output=True, stdin=subprocess.DEVNULL)
            self.assertTrue(marker.exists())

    def test_python_module_argv_both_forms(self):
        from taxjson.lib.dispatch import python_module_argv
        for legacy in (False, True):
            with self.subTest(legacy=legacy), \
                    tempfile.TemporaryDirectory() as tmp:
                marker = _plant(Path(tmp))
                argv = python_module_argv("taxjson.bin.taxjson_sort",
                                          ["--help"], legacy=legacy)
                r = subprocess.run(argv, cwd=tmp, env=ENV,
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
                self.assertFalse(marker.exists(), r.stderr)
                self.assertEqual(r.returncode, 0, r.stderr)
                # Run as -m would: usage names the program.
                self.assertIn("usage:", r.stdout)

    def test_legacy_bootstrap_argv0_is_the_module(self):
        from taxjson.lib.dispatch import python_module_argv
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkgdir"
            pkg.mkdir()
            (pkg / "probe_mod.py").write_text(
                "import sys\nprint(sys.argv)\nprint('' in sys.path)\n")
            env = dict(ENV, PYTHONPATH=str(pkg))
            r = subprocess.run(python_module_argv("probe_mod", ["a", "b"],
                                                  legacy=True),
                               cwd=tmp, env=env, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            argv_line, empty_on_path = r.stdout.splitlines()
            self.assertTrue(argv_line.endswith("'a', 'b']"), argv_line)
            self.assertIn("probe_mod.py", argv_line)
            self.assertEqual(empty_on_path, "False")

    def test_dispatch_subprocess_and_interactive_paths(self):
        # The corp-actions stage (interactive, a real subprocess) and
        # TAXJSON_DISPATCH=subprocess go through dispatch.run_cmd.
        from taxjson.lib import dispatch
        with tempfile.TemporaryDirectory() as tmp:
            marker = _plant(Path(tmp))
            cmd = [sys.executable, "-m", "taxjson.bin.taxjson_sort",
                   "--help"]
            old = dict(os.environ)
            os.environ.update(ENV)
            try:
                with open(Path(tmp) / "out.txt", "w") as f:
                    r = dispatch.run_cmd(cmd, stdout=f, cwd=tmp,
                                         interactive=True)
                self.assertEqual(r.returncode, 0)
                os.environ[dispatch._ENV_FLAG] = "subprocess"
                r = dispatch.run_cmd(cmd, capture_output=True, cwd=tmp)
                self.assertEqual(r.returncode, 0, r.stderr)
            finally:
                os.environ.clear()
                os.environ.update(old)
            self.assertFalse(marker.exists())

    def test_checklist_default_run_sub(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = _plant(root)
            old_cwd, old_env = os.getcwd(), dict(os.environ)
            os.chdir(root)
            os.environ.update(ENV)
            try:
                code, out, err = cl.default_run_sub(root)(["--version"])
            finally:
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_env)
            self.assertEqual(code, 0, err)
            self.assertFalse(marker.exists())

    def test_no_bare_dash_m_child_launch_in_the_package(self):
        # Every child Python launch goes through python_module_argv. A
        # `[sys.executable, "-m", "taxjson.bin.X"]` list is only the
        # tool-command marker dispatch.run_cmd takes (in process, or out
        # of process through python_module_argv): taxjson_run hands
        # every one to run_cmd.
        import re
        pat = re.compile(r"sys\.executable\s*,\s*[\"']-m[\"']")
        allowed = {"dispatch.py", "taxjson_run.py"}
        for p in (SRC / "taxjson").rglob("*.py"):
            if p.name not in allowed:
                self.assertIsNone(pat.search(p.read_text()), p)

if __name__ == "__main__":
    unittest.main()

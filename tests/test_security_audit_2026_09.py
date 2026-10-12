"""2026-09 security/privacy audit pins: owner-only file modes. (The
web UI's token went with the web UI; the `taxjson fetch` credential
pins moved to packages/taxjson-fetch with the fetcher.) All values
synthetic."""
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _run_loose(args, cwd):
    """Run a command under a permissive 022 umask, as a typical shell."""
    return subprocess.run(
        [sys.executable, "-c",
         "import os, runpy, sys; os.umask(0o022); sys.argv = sys.argv[1:]; "
         "runpy.run_module(sys.argv[0], run_name='__main__')", *args],
        cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)


class TestOwnerOnlyModes(unittest.TestCase):
    def test_init_creates_private_project_under_loose_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            r = _run_loose(["taxjson.bin.taxjson_run", "init", str(proj),
                            "--single", "--country", "canada"], cwd=REPO_ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(_mode(proj / "taxjson.toml"), 0o600)
            dirs = [d for d in (proj / "inputs").iterdir() if d.is_dir()]
            self.assertTrue(dirs)
            for d in dirs:
                self.assertEqual(_mode(d), 0o700, d)
            self.assertEqual(_mode(proj / "inputs"), 0o700)

    def test_entry_trampoline_sets_umask_then_calls_main(self):
        code = ("import os, sys; os.umask(0o022); "
                "from taxjson.bin._entry import taxjson_validate as f; "
                "sys.argv=['taxjson-validate', '--help']\n"
                "try:\n    f()\nexcept SystemExit:\n    pass\n"
                "m = os.umask(0); print(oct(m))")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "0o77")

    def test_every_console_script_resolves(self):
        doc = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        import importlib
        for name, target in doc["project"]["scripts"].items():
            mod, _, attr = target.partition(":")
            if mod == "taxjson.bin._entry":          # trampoline -> <attr>.main
                mod, attr = f"taxjson.bin.{attr}", "main"
            self.assertTrue(callable(getattr(importlib.import_module(mod), attr)), name)


if __name__ == "__main__":
    unittest.main()

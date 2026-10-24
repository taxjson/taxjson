"""The publish kit's dependency and code checks (publish-kit SPEC.md section
14) in this repo: scripts/kit_checks.py (an unchanged copy of the kit's
checks/kit_checks.py, which kit doctor compares), the kit-checks workflow that
runs it on every pull request of the dev repo, and kit-checks.toml, whose
exceptions name one advisory in one lock file or one rule in one path. The
check itself needs the network (OSV, the pinned tools): CI runs it; here the
configuration is checked offline."""
import datetime as dt
import re
import subprocess
import sys
import unittest
from pathlib import Path

from taxjson.lib.tomlcompat import tomllib

REPO = Path(__file__).resolve().parent.parent
TOML = REPO / "kit-checks.toml"
SCRIPT = REPO / "scripts" / "kit_checks.py"
WORKFLOW = REPO / ".github" / "workflows" / "kit-checks.yml"


def _config():
    if tomllib is None:                      # pragma: no cover
        raise unittest.SkipTest("no TOML reader")
    return tomllib.loads(TOML.read_text(encoding="utf-8"))


class TestConfig(unittest.TestCase):
    def test_every_lock_file_is_scanned(self):
        # scripts/lock_deps.py writes one lock per extra: a new one goes
        # into kit-checks.toml too, or osv-scanner never reads it.
        cfg = _config()
        locks = sorted(str(p.relative_to(REPO))
                       for p in (REPO / "requirements").glob("*.txt"))
        self.assertTrue(locks)
        self.assertEqual(sorted(cfg["lock_files"]), locks)
        for lock in cfg["lock_files"]:
            self.assertTrue((REPO / lock).is_file(), lock)

    def test_exceptions_are_exact_current_and_explained(self):
        cfg = _config()
        self.assertEqual(set(cfg) - {"lock_files", "vuln_exceptions",
                                     "code_exceptions"}, set())
        today = dt.date.today()
        for ex in cfg.get("code_exceptions", []):
            self.assertEqual(set(ex), {"rule", "path", "reason", "expires"}, ex)
            self.assertNotIn("*", ex["path"]); self.assertNotIn("..", ex["path"])
            self.assertTrue((REPO / ex["path"]).is_file(), ex["path"])
            self.assertIsInstance(ex["expires"], dt.date)
            self.assertGreaterEqual(ex["expires"], today,
                                    f"{ex['path']}: the exception expired; "
                                    "renew it or fix the hit")
            self.assertGreater(len(ex["reason"]), 20, ex)
            if ex["rule"].endswith("insecure-hash-algorithm-sha1"):
                # The excepted use is still there, and is what the reason says.
                self.assertIn("hashlib.sha1", (REPO / ex["path"]).read_text())
                self.assertIn("non-security fingerprint", ex["reason"])
        for ex in cfg.get("vuln_exceptions", []):
            self.assertEqual(set(ex), {"id", "lock_file", "reason", "expires"}, ex)
            self.assertIn(ex["lock_file"], cfg["lock_files"])
            self.assertGreaterEqual(ex["expires"], today, ex)

    def test_the_two_fingerprints_are_the_only_sha1_uses(self):
        # A new sha1 (or md5) use in src/ is a kit-checks hit: either the
        # value is a fingerprint stored and compared (except it here) or it
        # should be sha256.
        cfg = _config()
        excepted = {ex["path"] for ex in cfg.get("code_exceptions", [])
                    if ex["rule"].endswith(("sha1", "md5"))}
        found = set()
        for p in (REPO / "src").rglob("*.py"):
            if re.search(r"hashlib\.(sha1|md5)\(", p.read_text()):
                found.add(str(p.relative_to(REPO)))
        self.assertEqual(found, excepted)


class TestScriptAndWorkflow(unittest.TestCase):
    def test_the_script_is_the_kits_runnable_copy(self):
        self.assertTrue(SCRIPT.stat().st_mode & 0o100, "scripts/kit_checks.py is not executable")
        text = SCRIPT.read_text()
        self.assertIn("SCRIPT_PATH = \"scripts/kit_checks.py\"", text)
        self.assertIn('CONFIG = "kit-checks.toml"', text)
        r = subprocess.run([sys.executable, "-I", "-m", "py_compile", str(SCRIPT)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--help"],
                           capture_output=True, text=True)
        if sys.version_info >= (3, 11):
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--only", r.stdout)

    def test_the_workflow_runs_it_on_the_dev_repo_only(self):
        wf = WORKFLOW.read_text()
        self.assertRegex(wf, r"\nname: kit-checks\n")        # land.sh waits for it by this name
        self.assertIn("\non:\n  pull_request:", wf)
        self.assertIn("\n    if: github.event.repository.private\n", wf)
        self.assertIn("\n    runs-on: taxjson-dev", wf)
        self.assertIn("\n      - run: python3 -I scripts/kit_checks.py\n", wf)
        self.assertNotIn("${{", wf)

    def test_land_waits_for_a_workflow_whose_run_has_not_started(self):
        land = (REPO / "scripts" / "land.sh").read_text()
        self.assertIn("expected() {", land)
        self.assertIn("/^  pull_request:/", land)
        self.assertIn("paths(-ignore)?:", land)       # a filtered workflow is not waited for


if __name__ == "__main__":
    unittest.main()

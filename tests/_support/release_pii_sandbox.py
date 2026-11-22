"""Shared test helpers: a sandbox repository for check-pii.sh with a made-up account number and name.

Shared from: test_check_pii
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import _paths


REPO_ROOT = _paths.REPO
_BASH = shutil.which("bash")
_GIT = shutil.which("git")

# The fake "private" strings (assembled so this file never carries them).
_ACCT = "5550" + "4321"
_NAME = "Quinn" + " " + "Placeholder"


@unittest.skipUnless(_BASH and _GIT, "bash and git required")
class _Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / "scripts" / "hooks").mkdir(parents=True)
        shutil.copy(REPO_ROOT / "scripts" / "check-pii.sh", self.repo / "scripts")
        shutil.copy(REPO_ROOT / "scripts" / "hooks" / "pre-push",
                    self.repo / "scripts" / "hooks")
        self.deny = self.tmp / "denylist"
        self.deny.write_text(f"# test denylist\n{_ACCT}\n{_NAME}\n")
        self.env = dict(os.environ, TAXJSON_PII_DENYLIST=str(self.deny),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        HOME=str(self.tmp))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Sam Synthetic")
        self.git("config", "user.email", "sam@example.com")
        self.git("config", "core.hooksPath", os.devnull)
        # The scripts are the tool under test, not scanned content.
        (self.repo / ".gitignore").write_text("scripts/\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                           text=True, env=env or self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def scan(self, *args, stdin=None):
        return subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh"), *args],
                              cwd=self.repo, capture_output=True, text=True,
                              input=stdin, env=self.env)

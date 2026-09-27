"""scripts/check-pii.sh and scripts/hooks/pre-push, driven in a throwaway
git repository with a throwaway denylist (TAXJSON_PII_DENYLIST). Every
"private" value here is synthetic and assembled at run time."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
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


class TestTreeScan(_Sandbox):
    def test_denylist_case_insensitive_and_separator_tolerant(self):
        spaced = _ACCT[:4] + " " + _ACCT[4:]
        dashed = "-".join(_ACCT[i:i + 2] for i in range(0, 8, 2))
        for text in (spaced, dashed, _NAME.upper()):
            f = self.repo / "notes.txt"
            f.write_text(f"see {text} here\n")
            r = self.scan()
            self.assertEqual(r.returncode, 1, text)
            self.assertIn("private denylist match", r.stdout)
            self.assertNotIn(text, r.stdout)                 # masked
        (self.repo / "notes.txt").write_text("see 5550 4322 here\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_pii_ok_must_be_a_comment_marker(self):
        f = self.repo / "fixture.py"
        f.write_text(f'x = "{_ACCT}"  also pii-ok here\n')
        self.assertEqual(self.scan().returncode, 1)          # bare word: still a hit
        f.write_text(f'x = "{_ACCT}"  # pii-ok (synthetic)\n')
        self.assertEqual(self.scan().returncode, 0)
        f.write_text(f'x = "{_ACCT}"  (pii-ok: synthetic)\n')
        self.assertEqual(self.scan().returncode, 0)

    def test_eight_digit_filename_exempt_only_when_a_real_date(self):
        (self.repo / "stmt_20250131.csv").write_text("a\n")
        self.assertEqual(self.scan().returncode, 0)
        (self.repo / "stmt_20991399.csv").write_text("a\n")  # 13th month: an id
        r = self.scan()
        self.assertEqual(r.returncode, 1)
        self.assertIn("file NAME", r.stdout)


class TestDiffAndPush(_Sandbox):
    def _commit_all(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def test_pure_rename_scans_the_new_path(self):
        (self.repo / "notes.csv").write_text("a,b\n")
        self._commit_all()
        base = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "notes.csv", "stmt_55507777.csv")   # pii-ok (synthetic id shape)
        self._commit_all("rename")
        diff = self.git("diff", base, "HEAD")
        self.assertNotIn("+++ ", diff)                      # a PURE rename
        r = self.scan("--diff", stdin=diff)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME", r.stdout)

    def _bare_remote(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True,
                       env=self.env, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        return remote

    def _pre_push(self, lines):
        return subprocess.run(["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
                               "origin", "unused-url"], cwd=self.repo,
                              capture_output=True, text=True, input=lines, env=self.env)

    def test_identity_scan_blocks_others_warns_for_configured_user(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        first, last = _NAME.split()
        env = dict(self.env, GIT_AUTHOR_NAME=f"{first} {last}",
                   GIT_AUTHOR_EMAIL="q@example.com")
        self.git("commit", "-q", "-m", "c1", env=env)
        sha = self.git("rev-parse", "HEAD").strip()
        z = "0" * 40
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("IDENTITY", r.stderr)
        # Same identity configured as THIS repo's user: warn, don't block.
        self.git("config", "user.name", _NAME)
        self.git("config", "user.email", "q@example.com")
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING", r.stderr)

    def test_identity_scan_is_scoped_to_new_commits(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        env = dict(self.env, GIT_AUTHOR_NAME=_NAME, GIT_AUTHOR_EMAIL="q@example.com")
        self.git("commit", "-q", "-m", "old", env=env)
        old = self.git("rev-parse", "HEAD").strip()
        self.git("push", "-q", "--no-verify", "origin", "main")
        (self.repo / "b.txt").write_text("more\n")
        self._commit_all("new")
        new = self.git("rev-parse", "HEAD").strip()
        r = self._pre_push(f"refs/heads/main {new} refs/heads/main {old}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_annotated_tag_message_and_tagger_scanned(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self._commit_all()
        self.git("tag", "-a", "v1", "-m", f"release for {_NAME.lower()}")
        tag = self.git("rev-parse", "v1").strip()
        r = self._pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("TAG message", r.stderr)
        self.git("tag", "-d", "v1")
        env = dict(self.env, GIT_COMMITTER_NAME=_NAME, GIT_COMMITTER_EMAIL="q@example.com")
        self.git("tag", "-a", "v2", "-m", "clean message", env=env)
        tag = self.git("rev-parse", "v2").strip()
        r = self._pre_push(f"refs/tags/v2 {tag} refs/tags/v2 {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("IDENTITY", r.stderr)


if __name__ == "__main__":
    unittest.main()

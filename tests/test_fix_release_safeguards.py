"""Release safeguards (owner-approved, 2026-10-09).

scripts/release.sh publishes the GitHub release with notes that passed
`scripts/check-pii.sh --message` first. Every remote here is a local
temporary repository and `gh` is a stub on PATH that only records what
it was asked: nothing touches the network. Every "private" value is
synthetic and assembled at run time.
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_BASH = shutil.which("bash")
_GIT = shutil.which("git")
_Z = "0" * 40

# A synthetic "private" name for the throwaway denylist.
_NAME = "Quinn" + " " + "Placeholder"
# A money amount with thousands separators and cents, built at run time.
_AMOUNT = "4" + ",321.09"

# gh stand-in: `gh auth status` succeeds unless GH_STUB_AUTH=fail; every
# call is appended to $GH_STUB_LOG as one JSON list, and a `-F FILE`
# argument's contents are copied to $GH_STUB_LOG.notes.
_GH_STUB = r"""#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
args = sys.argv[1:]
log = os.environ["GH_STUB_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(args) + "\n")
if "-F" in args:
    with open(args[args.index("-F") + 1]) as src, open(log + ".notes", "w") as dst:
        dst.write(src.read())
PY
if [ "$1 $2" = "auth status" ] && [ "${GH_STUB_AUTH:-ok}" = fail ]; then
  echo "You are not logged into any GitHub hosts." >&2; exit 1
fi
exit 0
"""

# python stand-in for release.sh: pip is a no-op and `--version` reports
# pyproject.toml's version, as an editable install would.
_PY_STUB = r"""#!/usr/bin/env bash
if [ "$1 $2" = "-m pip" ]; then exit 0; fi
if [ "$1 $2 $3" = "-m taxjson.bin.taxjson_run --version" ]; then
  echo "taxjson $(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"; exit 0
fi
exit 3
"""


def _git_env(home):
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam Synthetic",
                GIT_AUTHOR_EMAIL="sam@example.com",
                GIT_COMMITTER_NAME="Sam Synthetic",
                GIT_COMMITTER_EMAIL="sam@example.com",
                GIT_TERMINAL_PROMPT="0", HOME=str(home))


@unittest.skipUnless(_BASH and _GIT, "bash and git required")
class _Sandbox(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.home = self.d / "home"
        self.home.mkdir()
        self.deny = self.d / "denylist"
        self.deny.write_text(f"# test denylist\n{_NAME}\n")
        self.env = dict(_git_env(self.home),
                        TAXJSON_PII_DENYLIST=str(self.deny),
                        TMPDIR=str(self.d))
        for k in ("TAXJSON_PII_AMOUNTS", "TAXJSON_PROMOTE_IGNORE_CI",
                  "TAXJSON_PROMOTE_TRAILERS", "TAXJSON_SLUG"):
            self.env.pop(k, None)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.gh_log = self.d / "gh.log"
        self.env["GH_STUB_LOG"] = str(self.gh_log)
        self.env["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        self._stub("gh", _GH_STUB)

    def tearDown(self):
        self._td.cleanup()

    def _stub(self, name, text, where=None):
        p = (where or self.bin) / name
        p.write_text(text)
        p.chmod(0o755)
        return p

    def git(self, cwd, *args, ok=True):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=self.env)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(x) for x in self.gh_log.read_text().splitlines()]

    def path_without_gh(self):
        """A PATH with every tool of the real one except gh."""
        farm = self.d / "nogh"
        farm.mkdir(exist_ok=True)
        for d in os.environ["PATH"].split(os.pathsep):
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if name == "gh" or (farm / name).exists():
                    continue
                src = os.path.join(d, name)
                if os.path.isfile(src) and os.access(src, os.X_OK):
                    os.symlink(src, farm / name)
        return str(farm)


class _ReleaseRepo(_Sandbox):
    """A bare origin and a clone holding release.sh, check-pii.sh, the
    pre-push hook, a stub ci.sh, both pyproject.toml files and a
    CHANGELOG with an Unreleased section."""

    def setUp(self):
        super().setUp()
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.dev = self.d / "dev"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        s = self.dev / "scripts"
        (s / "hooks").mkdir(parents=True)
        for f in ("release.sh", "check-pii.sh"):
            shutil.copy(REPO / "scripts" / f, s / f)
        shutil.copy(REPO / "scripts" / "hooks" / "pre-push",
                    s / "hooks" / "pre-push")
        self._stub("ci.sh", "#!/usr/bin/env bash\necho '== ci: PASS =='\n",
                   where=s)
        (self.dev / "pyproject.toml").write_text('version = "0.1.0"\n')
        fetch = self.dev / "packages" / "taxjson-fetch"
        fetch.mkdir(parents=True)
        (fetch / "pyproject.toml").write_text(
            'version = "0.1.0"\ndependencies = ["taxjson>=0.1.0"]\n')
        (self.dev / "docs").mkdir()
        (self.dev / "docs" / "troubleshooting.md").write_text(
            "# Troubleshooting\n")
        self.write_changelog("- **Faster runs** — the books build in "
                             "half the time.\n- Fix a typo.\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "start")
        self.git(self.dev, "tag", "-a", "v0.1.0", "-m", "taxjson v0.1.0")
        self.git(self.dev, "push", "-q", "origin", "main", "v0.1.0")
        self.py = self._stub("python-stub", _PY_STUB, where=self.d)
        self.env["PYTHON"] = str(self.py)

    def write_changelog(self, unreleased):
        (self.dev / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n" + unreleased
            + "\n## v0.1.0 (2026-01-01)\n\n- First.\n")

    def commit_changelog(self, unreleased):
        self.write_changelog(unreleased)
        self.git(self.dev, "commit", "-q", "-am", "changelog")
        self.git(self.dev, "push", "-q", "origin", "main")

    def release(self, *args, path=None):
        env = dict(self.env)
        if path:
            env["PATH"] = path
        return subprocess.run(
            ["bash", str(self.dev / "scripts" / "release.sh"), *args],
            cwd=self.d, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL)

    def origin_tags(self):
        return self.git(self.dev, "ls-remote", "--tags", "origin").count(
            "refs/tags/v0.2.0")


class TestReleaseNotes(_ReleaseRepo):
    def test_publishes_the_changelog_section_after_the_tag_push(self):
        r = self.release("v0.2.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.origin_tags(), "the tag is pushed")
        calls = [c for c in self.gh_calls() if c[:2] == ["release", "create"]]
        self.assertEqual(len(calls), 1, self.gh_calls())
        c = calls[0]
        self.assertEqual(c[:4], ["release", "create", "v0.2.0",
                                 "--verify-tag"])
        self.assertEqual(c[c.index("-t") + 1], "taxjson v0.2.0")
        notes = Path(str(self.gh_log) + ".notes").read_text()
        self.assertEqual(notes, "- **Faster runs** — the books build in "
                                "half the time.\n- Fix a typo.\n")
        self.assertFalse(Path(c[c.index("-F") + 1]).exists(),
                         "the notes file is removed once the release exists")
        self.assertNotIn("--repo", c)          # origin is not on GitHub here

    def test_names_the_github_repository(self):
        self.env["TAXJSON_SLUG"] = "example/taxjson"
        r = self.release("v0.2.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        c = [c for c in self.gh_calls() if c[:2] == ["release", "create"]][0]
        self.assertEqual(c[-2:], ["--repo", "example/taxjson"])

    def test_notes_file_overrides_the_changelog(self):
        notes = self.d / "my-notes.md"
        notes.write_text("Hand-written notes.\n")
        r = self.release("0.2.0", "--notes", str(notes))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(Path(str(self.gh_log) + ".notes").read_text(),
                         "Hand-written notes.\n")

    def test_a_scan_hit_stops_before_anything_is_pushed(self):
        # The CHANGELOG is clean; the hand-written notes carry an amount
        # and, in a second run, a denylisted name.
        for text in (f"Total {_AMOUNT} moved.\n", f"Thanks {_NAME}.\n"):
            notes = self.d / "bad-notes.md"
            notes.write_text(text)
            r = self.release("v0.2.0", "--notes", str(notes))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("release notes REFUSED", r.stdout)
            self.assertNotIn(_AMOUNT, r.stdout + r.stderr)
            self.assertNotIn(_NAME, r.stdout + r.stderr)
            self.assertFalse(self.origin_tags())
            self.assertEqual(self.git(self.dev, "tag", "-l", "v0.2.0"), "")
            self.assertFalse([c for c in self.gh_calls() if c[0] == "release"])
            self.git(self.dev, "checkout", "-q", "--", ".")

    def test_empty_section_is_refused(self):
        self.commit_changelog("")
        r = self.release("v0.2.0")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("release notes are empty", r.stdout)
        self.assertFalse(self.origin_tags())

    def test_without_gh_it_stops_with_the_exact_command(self):
        r = self.release("v0.2.0", path=self.path_without_gh())
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(self.origin_tags(), "the tag is pushed first")
        self.assertIn("NO GitHub release yet: gh is not installed", r.stdout)
        cmd = [x.strip() for x in r.stdout.splitlines()
               if "gh release create" in x][0]
        self.assertTrue(cmd.startswith("scripts/check-pii.sh --message < "),
                        cmd)
        self.assertIn('gh release create v0.2.0 --verify-tag -t '
                      '"taxjson v0.2.0" -F ', cmd)
        kept = Path(cmd.rsplit("-F ", 1)[1])
        self.assertIn("Faster runs", kept.read_text())

    def test_logged_out_gh_stops_with_the_command(self):
        self.env["GH_STUB_AUTH"] = "fail"
        r = self.release("v0.2.0")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not logged in (gh auth login)", r.stdout)
        self.assertIn("gh release create v0.2.0 --verify-tag", r.stdout)
        self.assertFalse([c for c in self.gh_calls() if c[0] == "release"])


class TestTagGuard(_Sandbox):
    """scripts/hooks/pre-push refuses any pushed tag that is not an
    annotated vX.Y.Z on main, and every tag delete or move."""

    def setUp(self):
        super().setUp()
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.repo = self.d / "repo"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.repo))
        self.git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.repo / "scripts" / "hooks").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "check-pii.sh",
                    self.repo / "scripts")
        shutil.copy(REPO / "scripts" / "hooks" / "pre-push",
                    self.repo / "scripts" / "hooks")
        (self.repo / ".gitignore").write_text("scripts/\n")
        self.commit("a")
        self.git(self.repo, "push", "-q", "origin", "main")
        self.a = self.git(self.repo, "rev-parse", "HEAD")

    def commit(self, name):
        (self.repo / f"{name}.txt").write_text(f"{name}\n")
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", name)
        return self.git(self.repo, "rev-parse", "HEAD")

    def hook(self, lines):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
             "origin", str(self.origin)], cwd=self.repo, capture_output=True,
            text=True, input=lines, env=self.env)

    def tag_line(self, name, remote_sha=_Z):
        sha = self.git(self.repo, "rev-parse", f"refs/tags/{name}")
        return f"refs/tags/{name} {sha} refs/tags/{name} {remote_sha}\n"

    def assertRefused(self, r, why):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("refusing to push these tags", r.stderr)
        self.assertIn(why, r.stderr)
        self.assertIn("never `git push --tags`", r.stderr)

    def test_annotated_release_on_main_passes(self):
        self.git(self.repo, "tag", "-a", "v1.2.3", "-m", "taxjson v1.2.3")
        r = self.hook(self.tag_line("v1.2.3"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_bad_names_and_lightweight_tags(self):
        for name in ("release-1", "v1.2", "v1.2.3-rc1", "v01.2.3x"):
            self.git(self.repo, "tag", "-a", name, "-m", "t")
            self.assertRefused(self.hook(self.tag_line(name)),
                               f"{name}: not a release tag (vX.Y.Z)")
        self.git(self.repo, "tag", "v1.2.4")
        self.assertRefused(self.hook(self.tag_line("v1.2.4")),
                           "v1.2.4: a lightweight tag")

    def test_commit_must_be_on_main(self):
        self.git(self.repo, "checkout", "-q", "-b", "side")
        self.commit("side")
        self.git(self.repo, "tag", "-a", "v1.2.5", "-m", "t")
        self.git(self.repo, "checkout", "-q", "main")
        self.assertRefused(self.hook(self.tag_line("v1.2.5")),
                           "v1.2.5: its commit is not on origin's main")
        # A new main commit is fine when the same push sends that main.
        c = self.commit("c")
        self.git(self.repo, "tag", "-a", "v1.2.6", "-m", "t")
        self.assertRefused(self.hook(self.tag_line("v1.2.6")),
                           "not on origin's main")
        main = f"refs/heads/main {c} refs/heads/main {self.a}\n"
        r = self.hook(main + self.tag_line("v1.2.6"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # ...but not a main pushed to another branch name.
        other = f"refs/heads/main {c} refs/heads/wip {_Z}\n"
        self.assertRefused(self.hook(other + self.tag_line("v1.2.6")),
                           "not on origin's main")

    def test_delete_and_move_refused(self):
        self.git(self.repo, "tag", "-a", "v1.2.3", "-m", "t")
        sha = self.git(self.repo, "rev-parse", "v1.2.3")
        r = self.hook(f"(delete) {_Z} refs/tags/v1.2.3 {sha}\n")
        self.assertRefused(r, "v1.2.3: deleting a tag")
        r = self.hook(self.tag_line("v1.2.3", remote_sha=self.a))
        self.assertRefused(r, "v1.2.3: moving a tag")

    def test_a_real_push_of_every_tag_is_refused(self):
        self.git(self.repo, "config", "core.hooksPath",
                 str(self.repo / "scripts" / "hooks"))
        self.git(self.repo, "tag", "-a", "v1.2.3", "-m", "taxjson v1.2.3")
        self.git(self.repo, "tag", "old-private-tag")
        r = subprocess.run(["git", "push", "-q", "origin", "--tags"],
                           cwd=self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("old-private-tag: not a release tag", r.stderr)
        self.assertEqual(self.git(self.repo, "ls-remote", "--tags", "origin"),
                         "")
        r = subprocess.run(["git", "push", "-q", "origin", "v1.2.3"],
                           cwd=self.repo, capture_output=True, text=True,
                           env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs/tags/v1.2.3",
                      self.git(self.repo, "ls-remote", "--tags", "origin"))


if __name__ == "__main__":
    unittest.main()

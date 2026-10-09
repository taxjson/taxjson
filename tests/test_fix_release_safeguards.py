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

# gh stand-in. Every call is appended to $GH_STUB_LOG as one JSON list,
# and a `-F FILE` argument's contents are copied to $GH_STUB_LOG.notes.
# `gh auth status` fails when GH_STUB_AUTH=fail. `gh api PATH` answers
# from the JSON map in $GH_STUB_API (the first key that is a substring of
# PATH; its value is the response body, or {"__fail__": 1} for an HTTP
# error), through the real jq when --jq is given. `gh run watch` exits
# $GH_STUB_WATCH (default 0).
_GH_STUB = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
log = os.environ["GH_STUB_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(args) + "\n")
if "-F" in args:
    with open(args[args.index("-F") + 1]) as src, open(log + ".notes", "w") as dst:
        dst.write(src.read())
if args[:2] == ["auth", "status"]:
    sys.exit(1 if os.environ.get("GH_STUB_AUTH") == "fail" else 0)
if args[:2] == ["run", "watch"]:
    sys.exit(int(os.environ.get("GH_STUB_WATCH", "0")))
if args[:1] == ["api"]:
    path = [a for a in args[1:] if not a.startswith("-")][0]
    table = json.load(open(os.environ["GH_STUB_API"])) if os.environ.get("GH_STUB_API") else {}
    for key, body in table.items():
        if key in path:
            break
    else:
        sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
    if isinstance(body, dict) and body.get("__fail__"):
        sys.stderr.write("gh: Server Error (HTTP 500)\n"); sys.exit(1)
    text = json.dumps(body)
    if "--jq" in args:
        r = subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=text,
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)
    sys.stdout.write(text + "\n")
sys.exit(0)
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


class _PromoteRepo(_Sandbox):
    """A bare origin with annotated v0.1.0..v0.3.0 on main and
    channels.json (stable v0.2.0, beta v0.3.0), and a clone holding
    scripts/promote.sh."""

    def setUp(self):
        super().setUp()
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.dev = self.d / "dev"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "promote.sh",
                    self.dev / "scripts" / "promote.sh")
        for i, tag in enumerate(("v0.1.0", "v0.2.0", "v0.3.0")):
            (self.dev / "f.txt").write_text(f"{i}\n")
            self.git(self.dev, "add", "-A")
            self.git(self.dev, "commit", "-q", "-m", f"release {tag}")
            self.git(self.dev, "tag", "-a", tag, "-m", f"taxjson {tag}")
        (self.dev / "channels.json").write_text(
            json.dumps({"stable": "v0.2.0", "beta": "v0.3.0"}, indent=2)
            + "\n")
        self.git(self.dev, "add", "channels.json")
        self.git(self.dev, "commit", "-q", "-m", "channels")
        self.git(self.dev, "push", "-q", "origin", "main", "v0.1.0",
                 "v0.2.0", "v0.3.0")
        self.api({})

    def api(self, table):
        f = self.d / "api.json"
        f.write_text(json.dumps(table))
        self.env["GH_STUB_API"] = str(f)

    @staticmethod
    def runs(*runs):
        return {"actions/workflows/tests.yml/runs": {"workflow_runs": [
            {"id": 4242, "status": st, "conclusion": co} for st, co in runs]}}

    def promote(self, *args, stdin="", path=None, **env):
        e = dict(self.env, **env)
        if path:
            e["PATH"] = path
        return subprocess.run(
            ["bash", str(self.dev / "scripts" / "promote.sh"), *args],
            cwd=self.d, capture_output=True, text=True, input=stdin, env=e)

    def head(self):
        return self.git(self.dev, "rev-parse", "HEAD")

    def origin_channels(self):
        self.git(self.dev, "fetch", "-q", "origin")
        return json.loads(self.git(self.dev, "show",
                                   "origin/main:channels.json"))

    def api_paths(self):
        return [c[1] for c in self.gh_calls() if c[0] == "api"]

    def assertRefused(self, r, needle, head=None):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        if head is not None:
            self.assertEqual(self.head(), head, "a refusal commits nothing")
        self.assertEqual(self.origin_channels(),
                         {"stable": "v0.2.0", "beta": "v0.3.0"})


class TestPromoteGates(_PromoteRepo):
    def test_forward_needs_green_ci_on_the_tags_commit(self):
        self.api(self.runs(("completed", "success")))
        r = self.promote("v0.3.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CI passed on v0.3.0", r.stdout)
        self.assertEqual(self.origin_channels()["stable"], "v0.3.0")
        sha = self.git(self.dev, "rev-parse", "v0.3.0^{commit}")
        runs = [x for x in self.api_paths() if "actions/workflows" in x]
        self.assertEqual(runs, [
            f"repos/taxjson/taxjson/actions/workflows/tests.yml/runs"
            f"?head_sha={sha}&event=push&branch=main&per_page=1"])

    def test_red_missing_or_failed_ci_refuses(self):
        h = self.head()
        self.api(self.runs(("completed", "failure")))
        self.assertRefused(self.promote("v0.3.0"),
                           "CI failed on v0.3.0 (failure): https://github.com/"
                           "taxjson/taxjson/actions/runs/4242", h)
        self.api(self.runs())
        self.assertRefused(self.promote("v0.3.0"), "No tests.yml run for "
                           "v0.3.0", h)
        self.api({"actions/workflows": {"__fail__": 1}})
        self.assertRefused(self.promote("v0.3.0"),
                           "Could not ask GitHub about CI", h)
        self.api(self.runs(("in_progress", None)))
        r = self.promote("v0.3.0", GH_STUB_WATCH="1")
        self.assertIn("still running", r.stdout)
        self.assertRefused(r, "CI failed on v0.3.0", h)
        self.assertRefused(self.promote("v0.3.0", path=self.path_without_gh()),
                           "the gh command is not installed", h)

    def test_running_ci_is_waited_for(self):
        self.api(self.runs(("in_progress", None)))
        r = self.promote("v0.3.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("still running", r.stdout)
        self.assertIn(["run", "watch", "4242", "--repo", "taxjson/taxjson",
                       "--exit-status"], self.gh_calls())

    def test_ignore_ci_warns_and_backwards_is_not_held_to_ci(self):
        r = self.promote("v0.3.0", TAXJSON_PROMOTE_IGNORE_CI="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING: GitHub Actions CI NOT checked", r.stderr)
        self.assertFalse([x for x in self.api_paths()
                          if "actions/workflows" in x])
        r = self.promote("v0.1.0", "beta", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.origin_channels()["beta"], "v0.1.0")
        self.assertFalse([x for x in self.api_paths()
                          if "actions/workflows" in x])

    def test_local_main_must_be_origin_main(self):
        self.api(self.runs(("completed", "success")))
        (self.dev / "wip.txt").write_text("work in progress\n")
        self.git(self.dev, "add", "wip.txt")
        self.git(self.dev, "commit", "-q", "-m", "wip")
        h = self.head()
        self.assertRefused(self.promote("v0.3.0"),
                           "Local main is not origin/main (1 commit(s) ahead, "
                           "0 behind)", h)
        self.git(self.dev, "push", "-q", "origin", "main")
        self.git(self.dev, "reset", "-q", "--hard", "HEAD~1")
        r = self.promote("v0.3.0")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("(0 commit(s) ahead, 1 behind)", r.stderr)

    def test_tag_must_be_annotated_on_origin_main(self):
        self.api(self.runs(("completed", "success")))
        h = self.head()
        self.git(self.dev, "tag", "v0.4.0")                 # lightweight
        self.git(self.dev, "push", "-q", "origin", "v0.4.0")
        self.assertRefused(self.promote("v0.4.0"), "v0.4.0 is a lightweight "
                           "tag", h)
        self.git(self.dev, "tag", "-a", "v0.5.0", "-m", "t")  # local only
        self.assertRefused(self.promote("v0.5.0"), "v0.5.0 is not on origin",
                           h)
        self.git(self.dev, "checkout", "-q", "-b", "side")
        (self.dev / "side.txt").write_text("side\n")
        self.git(self.dev, "add", "side.txt")
        self.git(self.dev, "commit", "-q", "-m", "side")
        self.git(self.dev, "tag", "-a", "v0.6.0", "-m", "t")
        self.git(self.dev, "push", "-q", "origin", "v0.6.0")
        self.git(self.dev, "checkout", "-q", "main")
        self.assertRefused(self.promote("v0.6.0"), "v0.6.0 is not on "
                           "origin/main", h)

    def test_refused_push_takes_the_promote_commit_back(self):
        self.api(self.runs(("completed", "success")))
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'main moved on' >&2\nexit 1\n")
        hook.chmod(0o755)
        (self.dev / "f.txt").write_text("staged, not promoted\n")
        self.git(self.dev, "add", "f.txt")
        h = self.head()
        r = self.promote("v0.3.0")
        self.assertRefused(r, "the promote commit is taken back", h)
        self.assertEqual(self.git(self.dev, "status", "--porcelain"),
                         "M  f.txt", "other work is left alone")
        self.assertEqual(json.loads((self.dev / "channels.json").read_text()),
                         {"stable": "v0.2.0", "beta": "v0.3.0"})


if __name__ == "__main__":
    unittest.main()

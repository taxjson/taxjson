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


def _public_api(releases=(), issues=(), issue_comments=(),
                review_comments=(), commit_comments=()):
    """gh api answers for what scripts/check-public.sh reads."""
    base = "repos/taxjson/taxjson/"
    return {base + "releases?": list(releases),
            base + "issues?": list(issues),
            base + "issues/comments?": list(issue_comments),
            base + "pulls/comments?": list(review_comments),
            base + "comments?": list(commit_comments)}


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
        for f in ("promote.sh", "check-public.sh", "check-pii.sh"):
            shutil.copy(REPO / "scripts" / f, self.dev / "scripts" / f)
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

    def api(self, table, public=None):
        """The stub's API answers: `table`, then what GitHub serves
        beside the code (empty unless `public` says otherwise)."""
        f = self.d / "api.json"
        f.write_text(json.dumps(dict(table, **_public_api(**(public or {})))))
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


class TestDevSetupHook(_Sandbox):
    """scripts/dev-setup.sh --hook-only installs the pre-push hook into
    the clone's own hooks folder (--git-common-dir), from a worktree too,
    whether or not a global core.hooksPath is set."""

    # The shape of a global hook that chains to the repo's own one.
    _CHAIN = ('#!/bin/sh\ninput="$(cat)"\n'
              'own="$(git rev-parse --git-common-dir)/hooks/pre-push"\n'
              'if [ -x "$own" ]; then printf \'%s\\n\' "$input" | "$own" "$@"; '
              'exit $?; fi\nexit 0\n')

    def setUp(self):
        super().setUp()
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.clone = self.d / "clone"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.clone))
        self.git(self.clone, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.clone / "scripts" / "hooks").mkdir(parents=True)
        for f in ("dev-setup.sh", "check-pii.sh"):
            shutil.copy(REPO / "scripts" / f, self.clone / "scripts" / f)
        shutil.copy(REPO / "scripts" / "hooks" / "pre-push",
                    self.clone / "scripts" / "hooks" / "pre-push")
        (self.clone / ".gitignore").write_text("scripts/\n")
        (self.clone / "a.txt").write_text("a\n")
        self.git(self.clone, "add", "-A")
        self.git(self.clone, "commit", "-q", "-m", "a")
        self.git(self.clone, "push", "-q", "origin", "main")
        self.wt = self.d / "wt"
        self.git(self.clone, "worktree", "add", "-q", "-b", "feature",
                 str(self.wt))
        # The worktree has its own copy of the (ignored) scripts.
        shutil.copytree(self.clone / "scripts", self.wt / "scripts")
        self.hook = self.clone / ".git" / "hooks" / "pre-push"

    def global_hooks(self, body):
        g = self.d / "globalhooks"
        g.mkdir(exist_ok=True)
        if body is not None:
            self._stub("pre-push", body, where=g)
        cfg = self.d / "gitconfig"
        cfg.write_text(f"[core]\n\thooksPath = {g}\n")
        self.env["GIT_CONFIG_GLOBAL"] = str(cfg)

    def setup_hook(self, cwd):
        return subprocess.run(
            ["bash", str(cwd / "scripts" / "dev-setup.sh"), "--hook-only"],
            cwd=cwd, capture_output=True, text=True, env=self.env)

    def test_from_a_worktree_with_a_chaining_global_hook(self):
        self.global_hooks(self._CHAIN)
        r = self.setup_hook(self.wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"pre-push hook installed: {self.hook}", r.stdout)
        self.assertIn("which chains to the hook above", r.stdout)
        self.assertNotIn("WARNING", r.stdout)
        self.assertTrue(os.access(self.hook, os.X_OK))
        self.assertIn("taxjson pre-push wrapper", self.hook.read_text())
        # A real push from the worktree: global hook -> the clone's
        # wrapper -> the worktree's own scripts/hooks/pre-push.
        self.git(self.wt, "tag", "stray-tag")
        r = subprocess.run(["git", "push", "-q", "origin", "stray-tag"],
                           cwd=self.wt, capture_output=True, text=True,
                           env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stray-tag: not a release tag", r.stderr)
        self.assertEqual(self.git(self.wt, "ls-remote", "--tags", "origin"),
                         "")
        # Idempotent: a re-run replaces its own wrapper.
        r = self.setup_hook(self.clone)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pre-push hook installed", r.stdout)

    def test_global_hook_that_does_not_chain_is_named(self):
        self.global_hooks("#!/bin/sh\nexit 0\n")
        r = self.setup_hook(self.clone)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.hook.is_file())
        self.assertIn("WARNING: core.hooksPath=", r.stdout)
        self.assertIn("will NOT run on push", r.stdout)

    def test_old_symlink_replaced_foreign_hook_left_alone(self):
        self.hook.parent.mkdir(exist_ok=True)
        os.symlink("../../scripts/hooks/pre-push", self.hook)
        r = self.setup_hook(self.clone)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.hook.is_symlink())
        self.assertIn("taxjson pre-push wrapper", self.hook.read_text())
        self.hook.write_text("#!/bin/sh\necho mine\n")
        r = self.setup_hook(self.clone)
        self.assertIn("already exists and is not ours", r.stdout)
        self.assertEqual(self.hook.read_text(), "#!/bin/sh\necho mine\n")


class TestSecretPatterns(_Sandbox):
    """scripts/check-pii.sh: known secret formats and a high-entropy value
    after a key / secret / token name; a pii-ok comment marker lets a
    test fixture through. Every secret here is assembled at run time."""

    def scan(self, text, mode="--text"):
        return subprocess.run(
            ["bash", str(REPO / "scripts" / "check-pii.sh"), mode],
            capture_output=True, text=True, input=text, env=self.env)

    RANDOM = "Zx9Qm2Lp7Rt4Vw8Ys3Bn6Kd1Hf5Jg0Ca"

    def test_known_formats_fire_and_stay_hidden(self):
        hits = {
            "pem rsa": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "pem pkcs8": "-----BEGIN " + "PRIVATE KEY-----",
            "pem openssh": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
            "anthropic": "key sk-" + "ant-api03-" + "aB3_dE5-" * 4,
            "slack": "SLACK " + "xoxb-" + "4071" * 3 + "-aB3dE5gH7k",
            "github oauth": "gho_" + "aB3dE5" * 6,
            "github server": "x ghs_" + "aB3dE5" * 6,
            "github user": "ghu_" + "aB3dE5" * 6,
            "github refresh": "ghr_" + "aB3dE5" * 6,
            "github pat": "github_" + "pat_" + "11ABCD" * 6,
            "aws": "id AKIA" + "ABCDEFGHIJ234567",
        }
        for label, text in hits.items():
            r = self.scan(text + "\n")
            self.assertEqual(r.returncode, 1, label + r.stdout)
            self.assertIn("credential-looking string", r.stdout, label)
            self.assertIn("<content hidden>", r.stdout, label)
            self.assertNotIn(text.split()[-1][6:], r.stdout, label)

    def test_high_entropy_value_after_a_key_name(self):
        hits = ["private_key: " + self.RANDOM,
                '{"apiKey": "' + self.RANDOM + '"}',
                "SLACK_BOT_TOKEN=" + self.RANDOM,
                "client_secrets = '" + self.RANDOM.lower() + "'"]
        for text in hits:
            r = self.scan(text + "\n")
            self.assertEqual(r.returncode, 1, text + r.stdout)
            self.assertIn("high-entropy value", r.stdout, text)
            self.assertNotIn(self.RANDOM[4:], r.stdout)
        misses = ["cache_key = " + "the_example_summary_for_2025_books",
                  "sort_key: " + "a" * 30 + "1",
                  "monkey = " + self.RANDOM[:20],
                  "keys = " + "ExampleRecord.__dataclass_fields__"]
        for text in misses:
            r = self.scan(text + "\n")
            self.assertEqual(r.returncode, 0, text + r.stdout)

    def test_pii_ok_marker_allows_a_fixture(self):
        pem = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertEqual(self.scan(pem + "\n").returncode, 1)
        self.assertEqual(self.scan(pem + "  # pii-ok (test fixture)\n")
                         .returncode, 0)
        line = "private_key = " + self.RANDOM
        diff = ("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n"
                "@@ -0,0 +1,2 @@\n+" + line + "\n+ok = 1\n")
        r = self.scan(diff, "--diff")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("high-entropy value", r.stdout)
        r = self.scan(diff.replace(line, line + "  # pii-ok"), "--diff")
        self.assertEqual(r.returncode, 0, r.stdout)


class TestSecretsWorkflow(unittest.TestCase):
    """The CI gitleaks job: a pinned, checksum-verified release, read-only,
    no persisted credentials, the base branch's allowlist on a PR."""

    def test_gitleaks_job(self):
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        job = wf[wf.index("\n  secrets:\n"):wf.index("\n  pr-commits:\n")]
        for part in ("V=8.28.0", "gitleaks_${V}_linux_x64.tar.gz",
                     "a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb"
                     "  gl.tgz\" | sha256sum -c -",
                     "permissions:\n      contents: read",
                     "persist-credentials: false", "fetch-depth: 0",
                     "timeout-minutes:", 'git show "$BASE:.gitleaks.toml"',
                     "--redact", "--exit-code 1"):
            self.assertIn(part, job)
        run = job[job.index("run: |"):]
        self.assertNotIn("${{", run)          # event data only via env
        # The checksum is verified before the binary is unpacked or run.
        self.assertLess(run.index("sha256sum -c"), run.index("tar -xzf"))
        cfg = (REPO / ".gitleaks.toml").read_text()
        self.assertIn("useDefault = true", cfg)
        self.assertIn("[[allowlists]]", cfg)

    @unittest.skipUnless(shutil.which("gitleaks"), "gitleaks not installed")
    def test_gitleaks_passes_the_tree(self):
        r = subprocess.run(["gitleaks", "dir", str(REPO / "tests"),
                            "--config", str(REPO / ".gitleaks.toml"),
                            "--redact", "--no-banner", "--exit-code", "1"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestWorkflowHygiene(unittest.TestCase):
    """Every CI job has a timeout and every checkout persists no token;
    the pull-request template asks for the gate and synthetic data."""

    def test_timeouts_and_checkouts(self):
        import re
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        jobs = wf[wf.index("\njobs:\n"):]
        blocks = re.split(r"\n  (?=[a-z][a-z0-9-]*:\n)", jobs)[1:]
        self.assertGreaterEqual(len(blocks), 6)
        for b in blocks:
            name = b.split(":", 1)[0]
            self.assertRegex(b, r"\n    timeout-minutes: \d+\n", name)
            steps = b.split("\n      - ")
            for st in steps:
                if "uses: actions/checkout@" in st:
                    self.assertIn("persist-credentials: false", st, name)

    def test_pull_request_template(self):
        t = (REPO / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
        self.assertIn("`scripts/ci.sh` passes locally", t)
        self.assertIn("- [ ] Test data is synthetic only", t)
        self.assertIn("No personal data", t)


class TestCheckPublic(_Sandbox):
    """scripts/check-public.sh reads what GitHub serves beside the code
    (stub gh, read-only) and scans it with check-pii.sh."""

    def setUp(self):
        super().setUp()
        self.env["TAXJSON_SLUG"] = "taxjson/taxjson"

    def run_check(self, **public):
        f = self.d / "api.json"
        f.write_text(json.dumps(_public_api(**public)))
        self.env["GH_STUB_API"] = str(f)
        return subprocess.run(["bash", str(REPO / "scripts" / "check-public.sh")],
                              capture_output=True, text=True, env=self.env)

    URL = "https://github.com/taxjson/taxjson/issues/"

    def test_clean_and_read_only(self):
        r = self.run_check(
            releases=[{"tag_name": "v1.0.0", "name": "taxjson v1.0.0",
                       "body": "- Faster runs.\r\n- Fix a typo."}],
            issues=[{"html_url": self.URL + "3", "title": "Crash on import",
                     "body": "Steps: run on the demo CSV."}],
            issue_comments=[{"html_url": self.URL + "3#issuecomment-9",
                             "body": "Thanks, fixed."}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("check-public: clean — what GitHub serves for "
                      "taxjson/taxjson: 1 releases, 1 issues, 1 "
                      "issue-comments, 0 review-comments, 0 commit-comments",
                      r.stdout)
        calls = [c for c in self.gh_calls() if c[0] == "api"]
        self.assertEqual(len(calls), 5)
        for c in calls:                       # GETs only
            self.assertEqual(c[:2], ["api", "--paginate"], c)
            self.assertFalse({"-X", "--method", "-f", "-F", "--input"} & set(c))

    def test_hits_are_masked_and_located(self):
        r = self.run_check(
            issues=[{"html_url": self.URL + "3", "title": "ok", "body": "ok"},
                    {"html_url": self.URL + "4", "title": "Import fails",
                     "body": f"Hi, I am {_NAME}.\nTotal {_AMOUNT}."}],
            review_comments=[{"html_url": self.URL.replace("issues", "pull")
                              + "5#discussion_r1",
                              "body": "mail me: quinn" + "@corp.io"}])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("!! issues of taxjson/taxjson:", r.stdout)
        self.assertIn("private denylist match", r.stdout)
        self.assertIn(f"   in {self.URL}4:\n", r.stdout)
        self.assertNotIn(f"in {self.URL}3:", r.stdout)
        self.assertIn("!! review-comments of taxjson/taxjson:", r.stdout)
        self.assertIn("pull/5#discussion_r1:", r.stdout)
        self.assertNotIn(_NAME, r.stdout + r.stderr)
        self.assertNotIn("corp.io", r.stdout + r.stderr)
        # An amount in an issue is the reporter's business; in a release
        # note (scanned as a message) it is refused.
        self.assertNotIn("money amount", r.stdout)
        r = self.run_check(releases=[{"tag_name": "v1.0.0", "name": "n",
                                      "body": f"Totals {_AMOUNT} now."}])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("money amount", r.stdout)
        self.assertIn("in release v1.0.0:", r.stdout)

    def test_cannot_check_fails_closed(self):
        f = self.d / "api.json"
        table = _public_api()
        table["repos/taxjson/taxjson/issues/comments?"] = {"__fail__": 1}
        f.write_text(json.dumps(table))
        self.env["GH_STUB_API"] = str(f)
        r = subprocess.run(["bash", str(REPO / "scripts" / "check-public.sh")],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("could not read the issue-comments", r.stderr)
        self.env["GH_STUB_AUTH"] = "fail"
        r = self.run_check()
        self.assertEqual(r.returncode, 2)
        self.assertIn("not logged in", r.stderr)
        env = dict(self.env, PATH=self.path_without_gh())
        r = subprocess.run(["bash", str(REPO / "scripts" / "check-public.sh")],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("gh is not installed", r.stderr)


class TestPromotePublicGate(_PromoteRepo):
    def test_a_hit_on_github_stops_a_forward_promote(self):
        self.api(self.runs(("completed", "success")), public={
            "issue_comments": [{"html_url": "https://github.com/taxjson/"
                                "taxjson/issues/2#issuecomment-1",
                                "body": f"signed, {_NAME}"}]})
        r = self.promote("v0.3.0")
        self.assertIn("!! issue-comments of taxjson/taxjson:", r.stdout)
        self.assertRefused(r, "scripts/check-public.sh refused")
        # A rollback is not held to it.
        r = self.promote("v0.1.0", "beta", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_skipped_with_a_warning_without_gh(self):
        self.env["GH_STUB_AUTH"] = "fail"
        r = self.promote("v0.3.0", TAXJSON_PROMOTE_IGNORE_CI="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("issues and comments on GitHub NOT scanned", r.stderr)
        self.assertEqual(self.origin_channels()["stable"], "v0.3.0")


if __name__ == "__main__":
    unittest.main()

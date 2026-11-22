"""Shared test helpers: a sandbox, a release repository (a bare origin and a clone), gh and python stubs and the public API's answers.

Shared from: test_fix_release_safeguards
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import _paths


REPO = _paths.REPO
_BASH = shutil.which("bash")
_GIT = shutil.which("git")

# A synthetic "private" name for the throwaway denylist.
_NAME = "Quinn" + " " + "Placeholder"

# gh stand-in. Every call is appended to $GH_STUB_LOG as one JSON list,
# and a `-F FILE` argument's contents are copied to $GH_STUB_LOG.notes.
# `gh auth status` fails when GH_STUB_AUTH=fail. `gh api PATH` answers
# from the JSON map in $GH_STUB_API (the first key that is a substring of
# PATH; its value is the response body, or {"__fail__": 1} for an HTTP
# error), through the real jq when --jq is given.
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
                  "TAXJSON_PROMOTE_TRAILERS", "TAXJSON_SLUG",
                  "TAXJSON_PUBLIC_REMOTE", "TAXJSON_PUBLIC_SLUG"):
            self.env.pop(k, None)
        # The throwaway origins take direct pushes (scripts/land.sh's
        # escape hatch for tests), and a missing CI run is not waited for.
        self.env["TAXJSON_LAND_DIRECT"] = "1"
        self.env["TAXJSON_CI_APPEAR_SECS"] = "0"
        self.env["TAXJSON_CI_POLL_SECS"] = "0"
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
        for f in ("release.sh", "check-pii.sh", "land.sh", "github-ci.sh",
                  "release-notes.sh"):
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
        # The public repo, as the publisher left it: v0.1.0 published.
        self.public = self.d / "public.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.public))
        self.git(self.dev, "push", "-q", str(self.public), "main", "v0.1.0")
        self.git(self.dev, "remote", "add", "public", str(self.public))
        self.git(self.dev, "remote", "set-url", "--push", "public",
                 "DISABLED")
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

    def origin_version(self):
        """pyproject.toml's version on origin's main."""
        self.git(self.dev, "fetch", "-q", "origin")
        return self.git(self.dev, "show", "origin/main:pyproject.toml")

    def prepare(self, *args, **kw):
        return self.release("v0.2.0", "--prepare", *args, **kw)

    def ci(self, *runs, then=None):
        """The stub's answer about tests.yml runs (default: one green);
        `then`: the run's own state once it has finished (polled)."""
        runs = runs or (("completed", "success"),)
        f = self.d / "api.json"
        table = {"actions/workflows/tests.yml/runs": {
            "workflow_runs": [{"id": 4242, "status": st, "conclusion": co}
                              for st, co in runs]}}
        if then:
            table["actions/runs/4242"] = {"status": then[0], "conclusion": then[1]}
        f.write_text(json.dumps(table))
        self.env["GH_STUB_API"] = str(f)

    def prepared(self):
        """Prepare v0.2.0 (the version commit lands on origin's main)."""
        r = self.prepare()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.git(self.dev, "rev-parse", "HEAD")

    def ci_paths(self):
        return [c[1] for c in self.gh_calls()
                if c[0] == "api" and "actions/workflows" in c[1]]

    def notes_left(self):
        """Notes files release.sh left in its TMPDIR."""
        return sorted(p.name for p in self.d.glob("taxjson-notes-*"))


def _public_api(releases=(), issues=(), issue_comments=(),
                review_comments=(), commit_comments=()):
    """gh api answers for what scripts/check-public.sh reads."""
    base = "repos/taxjson/taxjson/"
    return {base + "releases?": list(releases),
            base + "issues?": list(issues),
            base + "issues/comments?": list(issue_comments),
            base + "pulls/comments?": list(review_comments),
            base + "comments?": list(commit_comments)}

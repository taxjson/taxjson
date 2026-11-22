"""Shared test helpers: fixture bases: a bare origin with three releases and channels.json, its clone and a public repo, and install.sh run against it; git's environment for them; this tree's src/ first on sys.path.

Shared from: test_fix_release_channels
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _hermetic
import _paths

from _support.release_py_stub import _PY_STUB


def _src_first(repo):
    """REPO, its src/ put first on sys.path: importing this module does it
    before the importer's first taxjson import, so a test module run alone
    in a worktree whose editable install points at another checkout tests
    this one, whatever it takes from here (features.redact.test_redact_ids_land_checks_and_installer_tags takes
    no REPO) (TJ-4014)."""
    sys.path.insert(0, str(repo / "src"))
    return repo


REPO = _src_first(_paths.REPO)

GIT_ENV = {k: v for k, v in _hermetic.environ(
               GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam",
               GIT_AUTHOR_EMAIL="sam@example.com", GIT_COMMITTER_NAME="Sam",
               GIT_COMMITTER_EMAIL="sam@example.com",
               GIT_TERMINAL_PROMPT="0",
               # The throwaway origin takes direct pushes (no pull
               # requests): scripts/land.sh's escape hatch for tests.
               TAXJSON_LAND_DIRECT="1",
               # Captured, unwrapped: a message naming a long temp path
               # must not wrap inside a phrase a test looks for.
               TAXJSON_WIDTH="0").items()
           if k not in ("TAXJSON_DEV_DIR", "TAXJSON_PROD_DIR", "TAXJSON_CHANNEL",
                        "TAXJSON_OFFLINE", "TAXJSON_PROMOTE_TRAILERS", "TAXJSON_DIR",
                        "TAXJSON_BIN", "TAXJSON_DRY_RUN", "TAXJSON_REMEMBER_CHANNEL",
                        "TAXJSON_WITH_FETCH", "TAXJSON_PROMOTE_IGNORE_CI",
                        "TAXJSON_SLUG")}


def _channels_json(stable="v0.2.0", beta="v0.3.0"):
    return json.dumps({"stable": stable, "beta": beta}, indent=2) + "\n"


class _Repos(unittest.TestCase):
    """A bare `origin` (the dev repo) with three annotated releases on
    main and channels.json (stable v0.2.0, beta v0.3.0), a clone of it,
    and a bare `public` repo holding the same (what the publisher
    published), read only from the clone."""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git required")
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        # A gh that answers nothing: promote's GitHub checks never reach
        # the network from a test (tests/features/install/test_release_promote_and_secret_safeguards.py
        # drives them with a recording stub).
        nogh = self.d / "nogh"
        nogh.mkdir()
        (nogh / "gh").write_text("#!/bin/sh\necho 'gh: offline test stub' >&2\nexit 1\n")
        (nogh / "gh").chmod(0o755)
        self.path = f"{nogh}{os.pathsep}{os.environ['PATH']}"
        self.dev = self.d / "dev"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "promote.sh",
                    self.dev / "scripts" / "promote.sh")
        # promote.sh refuses to run without its pre-push PII gate.
        (self.dev / "scripts" / "hooks").mkdir()
        shutil.copy(REPO / "scripts" / "check-pii.sh",
                    self.dev / "scripts" / "check-pii.sh")
        shutil.copy(REPO / "scripts" / "hooks" / "pre-push",
                    self.dev / "scripts" / "hooks" / "pre-push")
        for f in ("land.sh", "github-ci.sh"):
            shutil.copy(REPO / "scripts" / f, self.dev / "scripts" / f)
        shutil.copy(REPO / "install.sh", self.dev / "install.sh")
        for i, tag in enumerate(("v0.1.0", "v0.2.0", "v0.3.0")):
            (self.dev / "f.txt").write_text(f"{i}\n")
            (self.dev / "CHANGELOG.md").write_text(
                f"# Changelog\n\n## Unreleased\n\n## {tag} (2026-01-0{i + 1})"
                f"\n\n- **Feature {i}** — what it does.\n- Fix {i}.\n")
            self.git(self.dev, "add", "-A")
            self.git(self.dev, "commit", "-q", "-m", f"release {tag}")
            self.git(self.dev, "tag", "-a", tag, "-m", f"taxjson {tag}")
        (self.dev / "channels.json").write_text(_channels_json())
        self.git(self.dev, "add", "channels.json")
        self.git(self.dev, "commit", "-q", "-m", "channels")
        self.git(self.dev, "push", "-q", "origin", "main", "--tags")
        self.public = self.d / "public.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.public))
        self.git(self.dev, "push", "-q", str(self.public), "main", "--tags")
        self.git(self.dev, "remote", "add", "public", str(self.public))
        self.git(self.dev, "remote", "set-url", "--push", "public",
                 "DISABLED")

    def publish(self, *refs):
        """What the publisher does: the public repo gets these refs."""
        self.git(self.dev, "push", "-q", str(self.public), *refs)

    def tearDown(self):
        self._td.cleanup()

    def git(self, cwd, *args):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=GIT_ENV)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def origin_channels(self):
        return json.loads(self.git(self.dev, "show",
                                   "origin/main:channels.json"))


class _Installer(_Repos):
    """_Repos with stub pythons, a HOME, an install folder and a bin
    folder, and install.sh run against the origin: the fixture of
    TestInstallerChannels, and of the installer tests elsewhere."""

    def setUp(self):
        super().setUp()
        self.stub = self.d / "stub"
        self.stub.mkdir()
        for name in ("python3", "python3.12", "python3.13"):
            (self.stub / name).write_text(_PY_STUB)
            (self.stub / name).chmod(0o755)
        self.home = self.d / "home"
        self.home.mkdir()
        self.inst = self.d / "inst"
        self.bin = self.d / "bin"
        self.chfile = self.home / ".config" / "taxjson" / "channel"

    def install(self, *args, ok=True, **env):
        e = dict(GIT_ENV, HOME=str(self.home), TAXJSON_REPO=str(self.origin),
                 TAXJSON_DIR=str(self.inst), TAXJSON_BIN=str(self.bin),
                 PATH=f"{self.stub}:{os.environ['PATH']}")
        e.update(env)
        r = subprocess.run(["bash", str(REPO / "install.sh"), *args], env=e,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def on(self):
        r = subprocess.run(["git", "-C", str(self.inst), "describe", "--tags",
                            "--exact-match"], capture_output=True, text=True)
        return r.stdout.strip() or self.git(self.inst, "rev-parse",
                                            "--abbrev-ref", "HEAD")

    def remembered(self):
        return self.chfile.read_text().strip()

    def set_origin_channels(self, stable, beta="v0.3.0"):
        (self.dev / "channels.json").write_text(_channels_json(stable, beta))
        self.git(self.dev, "commit", "-q", "-am", f"Promote {stable}")
        self.git(self.dev, "push", "-q", "origin", "main")

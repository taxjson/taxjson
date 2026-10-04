"""Release channels (owner request): tag → latest, promote → beta/stable.

channels.json parsing, scripts/promote.sh's refusals and commit, the
installer's channel resolution (stable default, beta, latest, dev, a
pinned vX.Y.Z, the remembered channel, never backwards, only annotated
tags on main), `taxjson channels --json`, and `promote` / `deploy`
refusing without a development checkout. Every git remote here is a
local temporary repository: nothing touches the network.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from taxjson.lib import channels as ch  # noqa: E402
from test_fix_misc_low import _PY_STUB  # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam",
               GIT_AUTHOR_EMAIL="sam@example.com", GIT_COMMITTER_NAME="Sam",
               GIT_COMMITTER_EMAIL="sam@example.com",
               GIT_TERMINAL_PROMPT="0")
for _k in ("TAXJSON_DEV_DIR", "TAXJSON_PROD_DIR", "TAXJSON_CHANNEL",
           "TAXJSON_OFFLINE", "TAXJSON_PROMOTE_TRAILERS", "TAXJSON_DIR",
           "TAXJSON_BIN", "TAXJSON_DRY_RUN", "TAXJSON_REMEMBER_CHANNEL"):
    GIT_ENV.pop(_k, None)


def _channels_json(stable="v0.2.0", beta="v0.3.0"):
    return json.dumps({"stable": stable, "beta": beta}, indent=2) + "\n"


class _Repos(unittest.TestCase):
    """A bare `origin` with three annotated releases on main and
    channels.json (stable v0.2.0, beta v0.3.0), and a clone of it."""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git required")
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.dev = self.d / "dev"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts").mkdir()
        shutil.copy(REPO / "scripts" / "promote.sh",
                    self.dev / "scripts" / "promote.sh")
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


# ------------------------------------------------------------ parsing
class TestParseChannels(unittest.TestCase):
    def test_valid_and_partial(self):
        self.assertEqual(ch.parse_channels(_channels_json()),
                         {"stable": "v0.2.0", "beta": "v0.3.0"})
        self.assertEqual(ch.parse_channels('{"stable": "v1.0.0"}'),
                         {"stable": "v1.0.0"})

    def test_refusals(self):
        for text, needle in (
                ("not json", "not valid JSON"),
                ('["v1.0.0"]', "must be a JSON object"),
                ('{"latest": "v1.0.0"}', "latest is always the newest"),
                ('{"gamma": "v1.0.0"}', "unknown channel"),
                ('{"stable": "1.0.0"}', "must name a release"),
                ('{"stable": "v1.0.0-rc1"}', "must name a release"),
                ('{"stable": 3}', "must name a release")):
            with self.subTest(text=text):
                with self.assertRaises(ch.ChannelsError) as cm:
                    ch.parse_channels(text, "channels.json on main")
                self.assertIn(needle, str(cm.exception))
                self.assertIn("channels.json", str(cm.exception))

    def test_release_tags_newest_first_and_only_vXYZ(self):
        self.assertEqual(
            ch.release_tags(["v0.9.0", "v0.10.0", "v0.11.0-rc1", "v1.0",
                             "v0.10.0.1", "x"]),
            ["v0.10.0", "v0.9.0"])

    def test_the_repos_channels_json(self):
        """channels.json on this branch parses; every release it names is
        a tag (when this clone has tags)."""
        got = ch.parse_channels((REPO / "channels.json").read_text())
        self.assertEqual(set(got), {"stable", "beta"})
        r = subprocess.run(["git", "-C", str(REPO), "tag", "-l", "v*"],
                           capture_output=True, text=True)
        if r.returncode or not r.stdout.strip():
            self.skipTest("no release tags in this clone")
        tags = set(r.stdout.split())
        for name, tag in got.items():
            self.assertIn(tag, tags, name)


# ------------------------------------------------------------ promote.sh
class TestPromote(_Repos):
    def promote(self, *args, stdin="", env=None):
        return subprocess.run(
            ["bash", str(self.dev / "scripts" / "promote.sh"), *args],
            cwd=self.d, capture_output=True, text=True, input=stdin,
            env=dict(GIT_ENV, **(env or {})))

    def head(self):
        return self.git(self.dev, "rev-parse", "HEAD")

    def test_forward_commits_channels_json_and_pushes(self):
        self.git(self.dev, "tag", "-a", "v0.4.0", "-m", "taxjson v0.4.0")
        self.git(self.dev, "push", "-q", "origin", "v0.4.0")
        (self.dev / "f.txt").write_text("staged, not promoted\n")
        self.git(self.dev, "add", "f.txt")
        tags = self.git(self.dev, "tag", "-l")
        r = self.promote("v0.4.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stable → v0.4.0", r.stdout)
        self.assertEqual(self.origin_channels(),
                         {"stable": "v0.4.0", "beta": "v0.3.0"})
        msg = self.git(self.dev, "log", "-1", "--format=%B", "origin/main")
        self.assertEqual(msg, "Promote v0.4.0 to stable")      # no trailers
        files = self.git(self.dev, "show", "--name-only", "--format=",
                         "origin/main")
        self.assertEqual(files, "channels.json")               # only it
        self.assertEqual(self.git(self.dev, "tag", "-l"), tags)  # no tag
        # beta, with trailers from the environment only.
        r = self.promote("0.4.0", "beta", env={
            "TAXJSON_PROMOTE_TRAILERS": "Co-Authored-By: Pat <p@example.com>"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.origin_channels()["beta"], "v0.4.0")
        msg = self.git(self.dev, "log", "-1", "--format=%B", "origin/main")
        self.assertEqual(msg, "Promote v0.4.0 to beta\n\n"
                              "Co-Authored-By: Pat <p@example.com>")
        r = self.promote("v0.4.0", "beta")
        self.assertEqual(r.returncode, 0)
        self.assertIn("already points at v0.4.0", r.stdout)

    def assertRefused(self, r, needle, head):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertEqual(self.head(), head, "a refusal commits nothing")
        self.assertEqual(self.origin_channels(),
                         {"stable": "v0.2.0", "beta": "v0.3.0"})

    def test_missing_tag(self):
        self.assertRefused(self.promote("v9.9.9"), "No tag v9.9.9",
                           self.head())

    def test_not_a_release_or_not_a_channel(self):
        h = self.head()
        self.assertRefused(self.promote("v0.3.0-rc1"), "A release is vX.Y.Z",
                           h)
        self.assertRefused(self.promote("v0.3.0", "latest"),
                           "latest is always the newest", h)
        self.assertRefused(self.promote("v0.3.0", "nightly"),
                           "Channel must be stable or beta", h)
        self.assertRefused(self.promote(), "usage", h)

    def test_not_on_main(self):
        self.git(self.dev, "checkout", "-q", "-b", "feature")
        self.assertRefused(self.promote("v0.3.0"), "Promote from main",
                           self.head())

    def test_dirty_channels_json(self):
        (self.dev / "channels.json").write_text(_channels_json("v0.3.0"))
        self.assertRefused(self.promote("v0.3.0"), "uncommitted changes",
                           self.head())

    def test_backwards_asks_first(self):
        h = self.head()
        self.assertRefused(self.promote("v0.1.0"), "Nothing changed", h)
        self.assertRefused(self.promote("v0.1.0", stdin="n\n"),
                           "Nothing changed", h)
        r = self.promote("v0.1.0", stdin="y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.origin_channels()["stable"], "v0.1.0")


# ------------------------------------------------------------ install.sh
class TestInstallerChannels(_Repos):
    def setUp(self):
        super().setUp()
        self.stub = self.d / "stub"
        self.stub.mkdir()
        for name in ("python3", "python3.9", "python3.10", "python3.11",
                     "python3.12", "python3.13"):
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
                 PATH=f"{self.stub}:{os.environ['PATH']}", **env)
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

    def test_stable_by_default_then_follows_it(self):
        r = self.install()
        self.assertEqual(self.on(), "v0.2.0")
        self.assertIn("channel stable → release v0.2.0", r.stdout)
        self.assertEqual(self.remembered(), "stable")
        self.assertTrue((self.bin / "taxjson").is_symlink())
        self.assertTrue((self.bin / "tjs").is_symlink())
        self.set_origin_channels("v0.3.0")
        r = self.install()
        self.assertEqual(self.on(), "v0.3.0", "re-running follows stable")
        self.assertIn("channel stable → release v0.3.0", r.stdout)

    def test_beta_latest_flag_and_env(self):
        self.install("--channel", "beta")
        self.assertEqual((self.on(), self.remembered()), ("v0.3.0", "beta"))
        self.install()                                   # remembered
        self.assertEqual(self.on(), "v0.3.0")
        self.git(self.dev, "tag", "-a", "v0.4.0", "-m", "taxjson v0.4.0")
        self.git(self.dev, "push", "-q", "origin", "v0.4.0")
        self.install(TAXJSON_CHANNEL="latest")
        self.assertEqual((self.on(), self.remembered()), ("v0.4.0", "latest"))
        # The pre-channels name of `latest`.
        self.install(TAXJSON_CHANNEL="release")
        self.assertEqual(self.remembered(), "latest")
        # The flag wins over the environment.
        r = self.install("--channel=beta", TAXJSON_CHANNEL="latest")
        self.assertEqual(self.remembered(), "beta")
        self.assertIn("newer than beta (v0.3.0) — staying", r.stdout)

    def test_never_backwards_but_a_pin_goes_back(self):
        self.install("--channel", "latest")
        self.assertEqual(self.on(), "v0.3.0")
        r = self.install("--channel", "stable")
        self.assertEqual(self.on(), "v0.3.0", "a channel never goes back")
        self.assertIn("on v0.3.0, newer than stable (v0.2.0)", r.stdout)
        self.assertEqual(self.remembered(), "stable")
        r = self.install("--channel", "0.1.0")
        self.assertEqual((self.on(), self.remembered()), ("v0.1.0", "v0.1.0"))
        self.assertIn("channel v0.1.0 → release v0.1.0", r.stdout)
        self.install()                                   # stays pinned
        self.assertEqual(self.on(), "v0.1.0")

    def test_dev_tracks_main(self):
        r = self.install("--channel", "dev")
        self.assertEqual(self.on(), "main")
        self.assertIn("channel dev → main @", r.stdout)
        self.install("--channel", "stable")
        self.assertEqual(self.on(), "v0.2.0")

    def test_no_channels_json_takes_the_newest(self):
        self.git(self.dev, "rm", "-q", "channels.json")
        self.git(self.dev, "commit", "-q", "-m", "no channels")
        self.git(self.dev, "push", "-q", "origin", "main")
        r = self.install()
        self.assertEqual(self.on(), "v0.3.0")
        self.assertIn("names no stable release yet", r.stdout)

    def test_refusals(self):
        r = self.install("--channel", "nightly", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown channel 'nightly'", r.stderr)
        self.assertFalse(self.inst.exists(), "refused before cloning")
        r = self.install("--channel", ok=False)
        self.assertEqual(r.returncode, 2)
        r = self.install("--channel", "v9.9.9", ok=False)
        self.assertIn("There is no release v9.9.9", r.stderr)
        # A lightweight tag, and an annotated one off main's history.
        self.git(self.dev, "tag", "v0.5.0")
        self.git(self.dev, "checkout", "-q", "--orphan", "side")
        (self.dev / "g.txt").write_text("x\n")
        self.git(self.dev, "add", "g.txt")
        self.git(self.dev, "commit", "-q", "-m", "side")
        self.git(self.dev, "tag", "-a", "v0.6.0", "-m", "taxjson v0.6.0")
        self.git(self.dev, "push", "-q", "origin", "v0.5.0", "v0.6.0")
        r = self.install("--channel", "v0.5.0", ok=False)
        self.assertIn("not an annotated release tag", r.stderr)
        r = self.install("--channel", "latest", ok=False)
        self.assertIn("v0.6.0 is not on the main branch", r.stderr)
        self.assertFalse(self.chfile.exists(), "a refusal remembers nothing")

    def test_dry_run_and_keep_channel(self):
        r = self.install("--channel", "beta", TAXJSON_DRY_RUN="1")
        self.assertIn("channel beta → release v0.3.0  (dry run", r.stdout)
        self.assertFalse(self.chfile.exists())
        self.assertFalse((self.bin / "taxjson").exists())
        self.install()
        self.install("--channel", "v0.3.0", TAXJSON_REMEMBER_CHANNEL="0")
        self.assertEqual((self.on(), self.remembered()), ("v0.3.0", "stable"))

    def test_help(self):
        r = self.install("--help")
        self.assertIn("--channel stable|beta|latest|dev|vX.Y.Z", r.stdout)
        self.assertIn("--with-fetch", r.stdout)

    def test_links_only_its_own(self):
        """Another program's `taxjson` / `tjs` link is left alone (security
        review L7); the installer's own links are re-pointed."""
        self.bin.mkdir()
        other = self.d / "other-taxjson"
        other.write_text("#!/bin/sh\n")
        (self.bin / "taxjson").symlink_to(other)
        (self.bin / "tjs").symlink_to(other)
        r = self.install()
        self.assertEqual(os.readlink(self.bin / "taxjson"), str(other))
        self.assertEqual(os.readlink(self.bin / "tjs"), str(other))
        self.assertIn("is another program's", r.stdout)
        # A plain file is left alone too (it used to stop the install).
        (self.bin / "taxjson").unlink()
        (self.bin / "taxjson").write_text("#!/bin/sh\n")
        self.install()
        self.assertFalse((self.bin / "taxjson").is_symlink())
        # Our own (even a stale one into this install) is replaced.
        (self.bin / "taxjson").unlink()
        (self.bin / "taxjson").symlink_to(self.inst / "venv" / "bin" / "gone")
        self.install()
        self.assertEqual(Path(os.readlink(self.bin / "taxjson")).name,
                         "taxjson")


# ------------------------------------------------------- the CLI verbs
class TestChannelsCommand(_Repos):
    def setUp(self):
        super().setUp()
        # 22 more releases so the default page (20) leaves some out.
        for i in range(22):
            self.git(self.dev, "tag", "-a", f"v0.0.{i + 1}", "-m",
                     f"taxjson v0.0.{i + 1}", "HEAD~3")
        self.git(self.dev, "push", "-q", "origin", "--tags")
        self.prod = self.d / "prod"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.prod))
        self.git(self.prod, "checkout", "-q", "v0.2.0")
        self.home = self.d / "home"
        (self.home / ".config" / "taxjson").mkdir(parents=True)
        (self.home / ".config" / "taxjson" / "channel").write_text("stable\n")

    def cli(self, *args, **env):
        e = dict(GIT_ENV, PYTHONPATH=str(SRC), HOME=str(self.home),
                 TAXJSON_DEV_DIR=str(self.dev),
                 TAXJSON_PROD_DIR=str(self.prod))
        e.update(env)
        return subprocess.run([sys.executable, "-m",
                               "taxjson.bin.taxjson_run", *args],
                              capture_output=True, text=True, env=e,
                              cwd=self.d, stdin=subprocess.DEVNULL)

    def test_json_shape(self):
        # A newer stable on origin than the local main knows: the fetch
        # brings it in.
        other = self.d / "other"
        self.git(self.d, "clone", "-q", str(self.origin), str(other))
        (other / "channels.json").write_text(_channels_json("v0.3.0"))
        self.git(other, "commit", "-q", "-am", "Promote v0.3.0 to stable")
        self.git(other, "push", "-q", "origin", "main")
        r = self.cli("channels", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual(set(st), {
            "source", "source_kind", "online", "offline_reason",
            "channels_file", "channels", "this_box", "problems", "releases",
            "total"})
        self.assertTrue(st["online"])
        self.assertEqual(st["source_kind"], "development checkout")
        self.assertEqual(st["channels"], {"stable": "v0.3.0",
                                          "beta": "v0.3.0",
                                          "latest": "v0.3.0"})
        self.assertEqual(st["channels_file"], "channels.json on origin/main")
        box = st["this_box"]
        self.assertEqual((box["installed"], box["release"], box["channel"]),
                         (True, "v0.2.0", "stable"))
        self.assertEqual(st["total"], 25)
        self.assertEqual(len(st["releases"]), 20)
        first = st["releases"][0]
        self.assertEqual(set(first), {"tag", "date", "subject", "marks"})
        self.assertEqual(first["tag"], "v0.3.0")
        self.assertEqual(first["marks"], ["stable", "beta", "latest"])
        self.assertEqual(first["subject"], "Feature 2 (+1 more)")
        self.assertEqual(st["releases"][1]["marks"], ["this-box"])
        self.assertEqual(st["problems"], [])
        r = self.cli("channels", "all", "--json")
        self.assertEqual(len(json.loads(r.stdout)["releases"]), 25)

    def test_offline_and_text(self):
        r = self.cli("channels", TAXJSON_OFFLINE="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(offline — showing what this clone already knows",
                      r.stdout)
        self.assertIn("  stable   v0.2.0", r.stdout)
        self.assertIn("this box v0.2.0", r.stdout)
        self.assertIn("←stable", r.stdout)
        self.assertIn("5 older — taxjson channels all", r.stdout)
        # An unreachable remote is "offline" too, never an error.
        self.git(self.dev, "remote", "set-url", "origin",
                 str(self.d / "gone.git"))
        r = self.cli("channels", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertFalse(st["online"])
        self.assertTrue(st["offline_reason"])

    def test_reads_the_production_copy_without_a_dev_checkout(self):
        # The package's own checkout counts as the development checkout
        # unless it IS the production copy.
        r = self.cli("channels", "--json", "--offline", TAXJSON_DEV_DIR="",
                     TAXJSON_PROD_DIR=str(REPO))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["source_kind"],
                         "production copy")

    def test_promote_and_deploy_refuse_without_a_dev_checkout(self):
        for verb in (("promote", "v0.3.0"), ("deploy",)):
            r = self.cli(*verb, TAXJSON_DEV_DIR="",
                         TAXJSON_PROD_DIR=str(REPO))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("is for the machine taxjson is developed on",
                          r.stderr)
            self.assertIn("re-run the installer", r.stderr)
        r = self.cli("promote", TAXJSON_DEV_DIR=str(self.d / "nowhere"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not a git checkout", r.stderr)

    def test_promote_defaults_to_what_this_box_runs(self):
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "promote",
             "beta"], capture_output=True, text=True, input="y\n",
            cwd=self.d, env=dict(GIT_ENV, PYTHONPATH=str(SRC),
                                 HOME=str(self.home),
                                 TAXJSON_DEV_DIR=str(self.dev),
                                 TAXJSON_PROD_DIR=str(self.prod)))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("promoting what this machine runs: v0.2.0", r.stdout)
        self.assertEqual(self.origin_channels()["beta"], "v0.2.0")

    def test_deploy_moves_the_production_copy_and_keeps_its_channel(self):
        stub = self.d / "stub"
        stub.mkdir()
        for name in ("python3", "python3.9", "python3.10", "python3.11",
                     "python3.12", "python3.13"):
            (stub / name).write_text(_PY_STUB)
            (stub / name).chmod(0o755)
        env = dict(PATH=f"{stub}:{os.environ['PATH']}",
                   TAXJSON_BIN=str(self.d / "bin"))
        r = self.cli("deploy", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("channel latest → release v0.3.0", r.stdout)
        on = self.git(self.prod, "describe", "--tags", "--exact-match")
        self.assertEqual(on, "v0.3.0")
        self.assertEqual((self.home / ".config" / "taxjson" / "channel")
                         .read_text().strip(), "stable")
        r = self.cli("deploy", "0.1.0", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # (v0.1.0's commit also carries the v0.0.x tags of setUp.)
        self.assertEqual(self.git(self.prod, "rev-parse", "HEAD"),
                         self.git(self.dev, "rev-parse", "v0.1.0^{commit}"))
        r = self.cli("deploy", "v1", **env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not a release", r.stderr)


class TestReleaseGroup(unittest.TestCase):
    def test_release_verbs_are_grouped_and_country_free(self):
        from taxjson.bin.taxjson_run import _COMMAND_GROUPS
        from taxjson.lib.country import COMMAND_COUNTRY
        groups = dict(_COMMAND_GROUPS)
        self.assertEqual(groups["Release"], ("channels", "deploy", "promote"))
        for c in groups["Release"]:
            self.assertNotIn(c, COMMAND_COUNTRY)

    def test_channels_sh_runs_the_same_page(self):
        r = subprocess.run(["bash", str(REPO / "scripts" / "channels.sh"),
                            "--offline", "--json"], capture_output=True,
                           text=True, env=dict(GIT_ENV,
                                               PYTHON=sys.executable))
        self.assertEqual(r.returncode, 0, r.stderr)
        st = json.loads(r.stdout)
        self.assertEqual(st["source"], str(REPO.resolve()))
        self.assertFalse(st["online"])


if __name__ == "__main__":
    unittest.main()

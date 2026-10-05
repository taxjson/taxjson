"""The installer installs taxjson-fetch by default (owner request).

taxjson-fetch stays its own package (an entry-point plugin with its own
dependencies) in the same release; install.sh puts it into the same
environment unless --without-fetch / TAXJSON_WITH_FETCH=0 says not to.
The opt-out is remembered in ~/.config/taxjson/fetch like the channel,
so a plain re-run (or `taxjson deploy`) keeps it out; --with-fetch /
TAXJSON_WITH_FETCH=1 (still accepted: old command lines and the
taxjson.com shim pass them) puts it back. A stub python records every
pip call; git remotes are local temporary repositories.
"""

import os
import subprocess
import unittest

from test_fix_release_channels import GIT_ENV, REPO, _Repos

REPO_INSTALL = REPO / "install.sh"

# python stand-in: a venv is a directory, pip calls are logged, and
# taxjson-fetch counts as installed while a marker file exists.
_PY_STUB = r"""#!/usr/bin/env bash
case "$1" in
  -c) exit 0 ;;
  --version) echo "Python 3.12.0" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; cp "$0" "$3/bin/python"
            printf '#!/bin/sh\necho "taxjson 0.0.0"\n' > "$3/bin/taxjson"
            chmod +x "$3/bin/taxjson"
            cp "$3/bin/taxjson" "$3/bin/tjs" ;;
      pip)
        mark="$(dirname "$0")/../fetch-installed"
        shift 2
        echo "pip $*" >> "$PIPLOG"
        case "$*" in
          show*taxjson-fetch*) [ -f "$mark" ] || exit 1 ;;
          uninstall*taxjson-fetch*) rm -f "$mark" ;;
          install*packages/taxjson-fetch*) touch "$mark" ;;
        esac ;;
    esac ;;
esac
exit 0
"""


class TestInstallerFetch(_Repos):
    def setUp(self):
        super().setUp()
        # v0.4.0 carries packages/taxjson-fetch; v0.1.0-v0.3.0 predate it.
        pkg = self.dev / "packages" / "taxjson-fetch"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "split")
        self.git(self.dev, "tag", "-a", "v0.4.0", "-m", "taxjson v0.4.0")
        self.git(self.dev, "push", "-q", "origin", "main", "--tags")
        self.stub = self.d / "stub"
        self.stub.mkdir()
        for name in ("python3", "python3.9", "python3.10", "python3.11",
                     "python3.12", "python3.13"):
            (self.stub / name).write_text(_PY_STUB)
            (self.stub / name).chmod(0o755)
        self.home = self.d / "home"
        self.home.mkdir()
        self.inst = self.d / "inst"
        self.piplog = self.d / "pip.log"
        self.fetchfile = self.home / ".config" / "taxjson" / "fetch"

    def install(self, *args, ok=True, **env):
        self.piplog.write_text("")
        e = dict(GIT_ENV, HOME=str(self.home), TAXJSON_REPO=str(self.origin),
                 TAXJSON_DIR=str(self.inst), TAXJSON_BIN=str(self.d / "bin"),
                 PIPLOG=str(self.piplog),
                 PATH=f"{self.stub}:{os.environ['PATH']}", **env)
        r = subprocess.run(["bash", str(REPO_INSTALL), *args], env=e,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def pip(self):
        return self.piplog.read_text().splitlines()

    def fetch_installs(self):
        return [c for c in self.pip()
                if c.startswith("pip install")
                and "packages/taxjson-fetch" in c]

    def installed(self):
        return (self.inst / "venv" / "fetch-installed").exists()

    def test_installed_by_default(self):
        r = self.install("--channel", "v0.4.0")
        self.assertEqual(len(self.fetch_installs()), 1, self.pip())
        self.assertIn(f"-e {self.inst}/packages/taxjson-fetch",
                      self.fetch_installs()[0])
        self.assertIn("taxjson-fetch installed:", r.stdout)
        self.assertNotIn("--with-fetch", r.stdout)
        self.assertFalse(self.fetchfile.exists(), "nothing to remember")
        # A plain re-run (an upgrade) installs it again.
        self.install()
        self.assertEqual(len(self.fetch_installs()), 1)
        self.assertTrue(self.installed())

    def test_with_fetch_is_still_accepted(self):
        for args, env in ((("--with-fetch",), {}),
                          ((), {"TAXJSON_WITH_FETCH": "1"})):
            with self.subTest(args=args, env=env):
                self.install("--channel", "v0.4.0", *args, **env)
                self.assertEqual(len(self.fetch_installs()), 1)
                self.assertFalse(self.fetchfile.exists())

    def test_without_fetch_skips_it_and_is_remembered(self):
        for args, env in ((("--without-fetch",), {}),
                          ((), {"TAXJSON_WITH_FETCH": "0"})):
            with self.subTest(args=args, env=env):
                if self.fetchfile.exists():
                    self.fetchfile.unlink()
                r = self.install("--channel", "v0.4.0", *args, **env)
                self.assertEqual(self.fetch_installs(), [], self.pip())
                self.assertFalse(self.installed())
                self.assertIn("taxjson-fetch left out", r.stdout)
                self.assertIn("auto-fetch is left out", r.stdout)
                self.assertEqual(self.fetchfile.read_text().strip(), "off")
                # A plain re-run (an upgrade, `taxjson deploy`) keeps it out.
                self.install()
                self.assertEqual(self.fetch_installs(), [])

    def test_opting_out_removes_it_and_with_fetch_brings_it_back(self):
        self.install("--channel", "v0.4.0")
        self.assertTrue(self.installed())
        r = self.install("--without-fetch")
        self.assertIn("taxjson-fetch removed", r.stdout)
        self.assertTrue(any(c.startswith("pip uninstall")
                            and "taxjson-fetch" in c for c in self.pip()))
        self.assertFalse(self.installed())
        self.install("--with-fetch")
        self.assertTrue(self.installed())
        self.assertFalse(self.fetchfile.exists(), "the opt-out is forgotten")
        self.install()
        self.assertEqual(len(self.fetch_installs()), 1)

    def test_flag_beats_environment_beats_memory(self):
        self.install("--channel", "v0.4.0", "--without-fetch",
                     TAXJSON_WITH_FETCH="1")
        self.assertEqual(self.fetch_installs(), [])
        self.install(TAXJSON_WITH_FETCH="1")
        self.assertEqual(len(self.fetch_installs()), 1)

    def test_a_release_before_the_split_keeps_the_note(self):
        r = self.install("--channel", "v0.2.0")
        self.assertEqual(self.fetch_installs(), [])
        self.assertIn("predates the taxjson-fetch split", r.stdout)
        self.assertNotIn("left out", r.stdout)

    def test_bad_value_and_dry_run(self):
        r = self.install(TAXJSON_WITH_FETCH="yes", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("use 1", r.stderr)
        self.assertFalse(self.inst.exists(), "refused before cloning")
        self.install("--without-fetch", TAXJSON_DRY_RUN="1")
        self.assertFalse(self.fetchfile.exists(), "a dry run remembers nothing")

    def test_usage(self):
        r = self.install("--help")
        self.assertIn("--without-fetch", r.stdout)
        self.assertIn("--with-fetch", r.stdout)
        self.assertIn("installed by default", r.stdout)
        r = self.install("--fetch", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--without-fetch", r.stderr)


if __name__ == "__main__":
    unittest.main()

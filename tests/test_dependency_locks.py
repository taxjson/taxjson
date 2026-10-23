"""Hash-pinned dependency locks (requirements/*.txt) and the installer.

install.sh installs every dependency from a lock with pip
--require-hashes (wheels only), then taxjson and taxjson-fetch with
--no-deps from the release checkout, so a release someone uploads to PyPI
later cannot reach a new install. scripts/lock_deps.py writes the locks;
here: the committed locks match pyproject.toml's dependencies and extras
(and taxjson-fetch's), nobody edited them by hand, and the installer's
pip calls — a hash mismatch stops it, a missing wheel falls back to the
pinned core only, nothing is ever installed unpinned. A stub python
records pip; git remotes are local temporary repositories.
"""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_fix_release_channels import GIT_ENV, REPO, _Repos

_spec = importlib.util.spec_from_file_location(
    "lock_deps", REPO / "scripts" / "lock_deps.py")
lock_deps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lock_deps)


class TestLocksMatchPyproject(unittest.TestCase):
    def test_committed_locks_match(self):
        self.assertEqual(lock_deps.check(), [],
                         "regenerate with scripts/lock_deps.py")

    def test_every_extra_and_the_core_have_a_lock(self):
        names = set(lock_deps.lock_inputs())
        self.assertTrue({"build", "core", "fx"} <= names)
        for path in (REPO / "requirements").glob("*.txt"):
            self.assertIn(path.stem, names)

    def test_build_lock_has_pip_and_the_build_backend(self):
        build = lock_deps.lock_inputs()["build"]
        self.assertIn("pip", build)
        self.assertTrue(any(lock_deps.req_name(r) == "setuptools"
                            for r in build))

    def test_fetch_dependencies_come_from_the_core_lock(self):
        # taxjson-fetch is installed --no-deps: what it needs besides
        # taxjson is part of the core lock's inputs.
        import re
        text = (REPO / "packages" / "taxjson-fetch" / "pyproject.toml"
                ).read_text()
        deps = re.search(r"^dependencies = \[(.*?)\]", text,
                         re.S | re.M).group(1)
        core = {lock_deps.req_name(r) for r in lock_deps.lock_inputs()["core"]}
        for req in re.findall(r'"([^"]+)"', deps):
            name = lock_deps.req_name(req)
            if name != "taxjson":
                self.assertIn(name, core)

    def test_installer_default_extra_has_a_lock(self):
        text = (REPO / "install.sh").read_text()
        self.assertIn('EXTRAS="${TAXJSON_EXTRAS-fx}"', text)
        self.assertTrue((REPO / "requirements" / "fx.txt").is_file())

    def test_installer_never_upgrades_pip_unpinned(self):
        text = (REPO / "install.sh").read_text()
        self.assertNotIn("--upgrade pip", text)


class TestCheckCatchesDrift(unittest.TestCase):
    """check() on a copy: an extra added to pyproject.toml, a hand edit,
    a lock left for a removed extra, a pin without hashes."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.locks = self.d / "requirements"
        shutil.copytree(REPO / "requirements", self.locks)
        self.pyproject = self.d / "pyproject.toml"
        shutil.copy(REPO / "pyproject.toml", self.pyproject)
        self._saved = lock_deps.CORE_PYPROJECT
        lock_deps.CORE_PYPROJECT = self.pyproject

    def tearDown(self):
        lock_deps.CORE_PYPROJECT = self._saved
        self._td.cleanup()

    def problems(self):
        return "\n".join(lock_deps.check(self.locks))

    def test_clean_copy_passes(self):
        self.assertEqual(self.problems(), "")

    def test_changed_extra(self):
        text = self.pyproject.read_text().replace(
            '"yfinance>=0.2",\n    "pandas>=1.5",\n]',
            '"yfinance>=0.2",\n    "pandas>=1.5",\n    "rich>=13",\n]', 1)
        self.assertIn("rich>=13", text)
        self.pyproject.write_text(text)
        p = self.problems()
        self.assertIn("requirements/fx.txt was locked from other requirements", p)
        self.assertIn("does not pin rich", p)
        self.assertIn("scripts/lock_deps.py", p)

    def test_new_and_removed_extra(self):
        text = self.pyproject.read_text().replace(
            "[project.optional-dependencies]\n",
            '[project.optional-dependencies]\nplots = ["matplotlib>=3"]\n', 1)
        self.pyproject.write_text(text)
        (self.locks / "gone.txt").write_text("")
        p = self.problems()
        self.assertIn("requirements/plots.txt is missing", p)
        self.assertIn("requirements/gone.txt: no such extra", p)

    def test_hand_edit(self):
        f = self.locks / "core.txt"
        f.write_text(f.read_text().replace("tomli==", "tomli==9", 1))
        self.assertIn("edited by hand", self.problems())

    def test_pin_without_hash(self):
        f = self.locks / "build.txt"
        fields, body = lock_deps.split_lock(f.read_text())
        body = body + "wheel==0.1\n"
        head = f.read_text().split("\n\n", 1)[0].replace(
            fields["body-sha256"], lock_deps.body_digest(body))
        f.write_text(head + "\n\n" + body)
        self.assertIn("wheel==0.1 has no --hash", self.problems())


# python stand-in: a venv is a directory; pip calls are logged; PIP_MODE
# makes the extras step (an -r of fx.txt) fail the way pip does.
_PY_STUB = r"""#!/usr/bin/env bash
case "$1" in
  -c) exit 0 ;;
  --version) echo "Python 3.12.0" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; cp "$0" "$3/bin/python"
            printf '#!/bin/sh\necho "taxjson 0.0.0"\n' > "$3/bin/taxjson"
            chmod +x "$3/bin/taxjson"; cp "$3/bin/taxjson" "$3/bin/tjs" ;;
      pip)
        shift 2
        echo "pip $*" >> "$PIPLOG"
        case "$*" in show*) exit 1 ;; esac
        case "$*" in *fx.txt*)
          case "$PIP_MODE" in
            hash) echo "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE." >&2
                  echo "    six==1.17.0 from https://files.example/six.whl" >&2; exit 1 ;;
            nowheel) echo "ERROR: Could not find a version that satisfies the requirement pandas==9.9 (from versions: 1.0)" >&2
                     echo "ERROR: No matching distribution found for pandas==9.9" >&2; exit 1 ;;
            network) echo "WARNING: Retrying (Retry(total=4)) after connection broken by 'NewConnectionError'" >&2
                     echo "ERROR: Could not find a version that satisfies the requirement pandas==9.9 (from versions: none)" >&2
                     echo "ERROR: No matching distribution found for pandas==9.9" >&2; exit 1 ;;
          esac ;;
        esac ;;
    esac ;;
esac
exit 0
"""


def _write_locks(root):
    req = root / "requirements"
    req.mkdir(exist_ok=True)
    for name in ("build", "core", "fx", "xlsx"):
        (req / f"{name}.txt").write_text(f"# lock {name}\n")


class TestInstallerUsesTheLocks(_Repos):
    def setUp(self):
        super().setUp()
        # v0.4.0 has the locks and the fetch plugin; v0.1.0-v0.3.0 do not.
        pkg = self.dev / "packages" / "taxjson-fetch"
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        _write_locks(self.dev)
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "locks")
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

    def install(self, *args, ok=True, **env):
        self.piplog.write_text("")
        e = dict(GIT_ENV, HOME=str(self.home), TAXJSON_REPO=str(self.origin),
                 TAXJSON_DIR=str(self.inst), TAXJSON_BIN=str(self.d / "bin"),
                 PIPLOG=str(self.piplog),
                 PATH=f"{self.stub}:{os.environ['PATH']}")
        e.pop("TAXJSON_EXTRAS", None)
        e.update(env)
        r = subprocess.run(["bash", str(REPO / "install.sh"), *args], env=e,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def installs(self):
        return [c for c in self.piplog.read_text().splitlines()
                if c.startswith("pip install")]

    def assertAllPinned(self, calls):
        for c in calls:
            if "-r " in c:
                self.assertIn("--require-hashes", c)
                self.assertIn("--only-binary :all:", c)
            else:
                # Only our own checkout, never resolved from an index.
                self.assertIn("--no-deps", c)
                self.assertIn(" -e ", c)
                self.assertNotIn("[", c)
        self.assertFalse(any("--upgrade" in c for c in calls), calls)

    def test_default_extras(self):
        self.install("--channel", "v0.4.0")
        calls = self.installs()
        self.assertAllPinned(calls)
        self.assertIn("requirements/build.txt", calls[0])
        self.assertIn("requirements/core.txt -r ", calls[1])
        self.assertIn("requirements/fx.txt", calls[1])
        self.assertIn(f"--no-build-isolation -e {self.inst}", calls[2])
        self.assertIn("packages/taxjson-fetch", calls[3])
        self.assertEqual(len(calls), 4, calls)

    def test_no_extras_and_several(self):
        self.install("--channel", "v0.4.0", TAXJSON_EXTRAS="")
        calls = self.installs()
        self.assertAllPinned(calls)
        self.assertTrue(calls[1].endswith("requirements/core.txt"), calls)
        self.install("--channel", "v0.4.0", TAXJSON_EXTRAS="fx, xlsx")
        calls = self.installs()
        self.assertIn("fx.txt", calls[1])
        self.assertIn("xlsx.txt", calls[1])

    def test_unknown_extra_is_refused(self):
        r = self.install("--channel", "v0.4.0", ok=False,
                         TAXJSON_EXTRAS="nope")
        self.assertIn("no extra [nope]", r.stderr)
        self.assertIn("fx xlsx", r.stderr)
        for bad in ("core", "../core"):
            r = self.install("--channel", "v0.4.0", ok=False,
                             TAXJSON_EXTRAS=bad)
            self.assertIn(f"no extra [{bad}]", r.stderr)

    def test_hash_mismatch_stops_the_install(self):
        r = self.install("--channel", "v0.4.0", ok=False, PIP_MODE="hash")
        self.assertIn("does not match the sha256", r.stderr)
        self.assertIn("Nothing unpinned was installed", r.stderr)
        self.assertIn("DO NOT MATCH THE HASHES", r.stderr)
        calls = self.installs()
        self.assertAllPinned(calls)
        self.assertEqual(len(calls), 2, "nothing after the mismatch")
        self.assertFalse((self.d / "bin" / "taxjson").exists())

    def test_missing_wheel_falls_back_to_the_pinned_core(self):
        r = self.install("--channel", "v0.4.0", PIP_MODE="nowheel")
        self.assertIn("extras [fx] NOT installed: no prebuilt wheel of "
                      "pandas==9.9", r.stdout)
        self.assertIn("installing the core package only", r.stdout)
        calls = self.installs()
        self.assertAllPinned(calls)
        self.assertTrue(calls[2].endswith("requirements/core.txt"), calls)

    def test_network_error_is_not_a_missing_wheel(self):
        r = self.install("--channel", "v0.4.0", ok=False, PIP_MODE="network")
        self.assertIn("Check the network", r.stderr)
        self.assertNotIn("core package only", r.stdout)

    def test_older_release_uses_mains_locks(self):
        # v0.3.0 has no requirements/: main's locks pin its dependencies.
        r = self.install("--channel", "v0.3.0")
        self.assertIn("predates the dependency locks — using main's",
                      r.stdout)
        calls = self.installs()
        self.assertAllPinned(calls)
        self.assertIn("venv/.locks-from-main/fx.txt", calls[1])
        self.assertTrue((self.inst / "venv" / ".locks-from-main"
                         / "core.txt").is_file())

    def test_no_locks_anywhere_says_so(self):
        for name in ("build", "core", "fx", "xlsx"):
            self.git(self.dev, "rm", "-q", f"requirements/{name}.txt")
        self.git(self.dev, "commit", "-q", "-m", "no locks")
        self.git(self.dev, "push", "-q", "origin", "main")
        r = self.install("--channel", "v0.3.0")
        self.assertIn("dependencies come from PyPI unpinned", r.stdout)


if __name__ == "__main__":
    unittest.main()


class TestLockTool(unittest.TestCase):
    """Options of scripts/lock_deps.py that need no network."""

    def test_relative_uv_path_is_made_absolute(self):
        # uv runs from a temporary folder: a relative --uv would not be found.
        rel = os.path.join("venv", "bin", "uv")
        self.assertEqual(lock_deps.find_uv(rel), os.path.abspath(rel))
        self.assertEqual(lock_deps.find_uv("uv"), "uv")

    def test_security_update_lets_one_package_past_the_cooldown(self):
        calls = []

        def fake_run(cmd, cwd):
            calls.append(cmd)
            if cmd[1:] == ["--version"]:
                return subprocess.CompletedProcess(cmd, 0, "uv 0.0.0\n", "")
            out = Path(cwd) / cmd[cmd.index("-o") + 1]
            out.write_text("certifi==1 \\\n    --hash=sha256:" + "0" * 64 + "\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as td:
            saved = lock_deps.run, lock_deps.LOCK_DIR
            lock_deps.run, lock_deps.LOCK_DIR = fake_run, Path(td)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    lock_deps.compile_locks("uv", 7, ["Certifi", " "])
            finally:
                lock_deps.run, lock_deps.LOCK_DIR = saved
            header = (Path(td) / "core.txt").read_text().split("\n\n")[0]
        compiles = [c for c in calls if "compile" in c]
        self.assertTrue(compiles)
        for c in compiles:
            self.assertIn("--exclude-newer", c)
            i = c.index("--exclude-newer-package")
            self.assertTrue(c[i + 1].startswith("certifi="), c[i + 1])
            self.assertEqual(c.count("--exclude-newer-package"), 1)
        self.assertIn("# security-updates: certifi", header)

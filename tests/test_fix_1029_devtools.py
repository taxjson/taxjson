"""Backlog fix round 2026-10-29, group `devtools`: the developer scripts
(land.sh, release.sh, check-pii.sh, ci.sh, ci_no_extras.sh) and the
checks that keep the local gate as strict as CI.

Every script runs in a throwaway folder: git remotes are local bare
repositories and `gh` is a stub on PATH, so nothing touches the network
or the real repository. Every value is synthetic.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_check_pii import _Sandbox as _PiiSandbox
from test_fix_release_safeguards import _ReleaseRepo

REPO = Path(__file__).resolve().parent.parent
_BASH = shutil.which("bash")
_GIT = shutil.which("git")


def _run(*args, cwd=None, env=None, timeout=120, stdin=None):
    return subprocess.run(list(args), check=False, cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=timeout,
                          input=stdin, stdin=None if stdin is not None
                          else subprocess.DEVNULL)


def _git_env(home):
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam Synthetic",
                GIT_AUTHOR_EMAIL="sam@example.com",
                GIT_COMMITTER_NAME="Sam Synthetic",
                GIT_COMMITTER_EMAIL="sam@example.com",
                GIT_TERMINAL_PROMPT="0", HOME=str(home))


# A gh wrapper around test_fix_release_hardening's land.sh stub: another
# commit reaches origin's main, as a pull request merged meanwhile would —
# on land.sh's first `gh pr view` (LAND_MOVE=view: during the wait) or on
# its own `gh pr merge` (LAND_MOVE=merge: between its last look and the
# merge).
_MOVING_MAIN_GH = r"""#!/usr/bin/env python3
import os, subprocess, sys, tempfile
args = sys.argv[1:]
flag = os.environ["LAND_MOVE_FLAG"]
when = os.environ.get("LAND_MOVE", "")
hit = ((when == "view" and args[:2] == ["pr", "view"])
       or (when == "merge" and args[:2] == ["pr", "merge"] and "--auto" not in args
           and "--disable-auto" not in args))
if hit and not os.path.exists(flag):
    open(flag, "w").close()
    with tempfile.TemporaryDirectory() as td:
        run = lambda *a: subprocess.run(["git", *a], cwd=td, check=True, capture_output=True)
        run("clone", "-q", os.environ["LAND_ORIGIN"], ".")
        with open(os.path.join(td, "other.txt"), "w") as f:
            f.write("merged meanwhile\n")
        run("add", "other.txt")
        run("commit", "-q", "-m", "another pull request")
        run("push", "-q", "origin", "main")
os.execv(sys.executable, [sys.executable, os.environ["LAND_GH_INNER"], *args])
"""


@unittest.skipUnless(_BASH and _GIT, "bash and git required")
class TestAutoMergeOnlyWhenMainMustBeUpToDate(unittest.TestCase):
    """TJ-0214: with main's rules requiring checks, land.sh handed the
    merge to GitHub's auto-merge, which rebases the commits onto whatever
    main is when the checks pass; a main that moved after land.sh's last
    look was merged onto, and land.sh called it landed. Auto-merge is now
    used only when the rules also require a branch up to date with main
    (GitHub then refuses that merge); else land.sh merges itself, only onto
    the gated base, and warns when the commits landed on top of others."""

    def setUp(self):
        from test_fix_release_hardening import _LAND_GH
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        home = self.d / "home"
        home.mkdir()
        deny = self.d / "denylist"
        deny.write_text("# test denylist\n")
        self.origin = self.d / "origin.git"
        self.dev = self.d / "dev"
        self.env = dict(_git_env(home), TAXJSON_PII_DENYLIST=str(deny),
                        TMPDIR=str(self.d), TAXJSON_LAND_POLL_SECS="0",
                        LAND_STATE=str(self.d / "land.json"),
                        LAND_ORIGIN=str(self.origin), GH_STUB_LOG=str(self.d / "gh.log"),
                        LAND_MOVE_FLAG=str(self.d / "moved"),
                        LAND_GH_INNER=str(self.d / "gh-inner"))
        for k in ("TAXJSON_LAND_DIRECT", "TAXJSON_SLUG", "TAXJSON_PII_AMOUNTS",
                  "LAND_MOVE"):
            self.env.pop(k, None)
        (self.d / "gh-inner").write_text(_LAND_GH)
        (self.d / "bin").mkdir()
        gh = self.d / "bin" / "gh"
        gh.write_text(_MOVING_MAIN_GH)
        gh.chmod(0o755)
        self.env["PATH"] = f"{self.d / 'bin'}:{os.environ['PATH']}"
        self.git(self.d, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts" / "hooks").mkdir(parents=True)
        for f in ("land.sh", "check-pii.sh", "hooks/pre-push"):
            shutil.copy(REPO / "scripts" / f, self.dev / "scripts" / f)
        (self.dev / "f.txt").write_text("0\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "start")
        self.git(self.dev, "push", "-q", "origin", "main")
        (self.dev / "f.txt").write_text("a change\n")
        self.git(self.dev, "commit", "-q", "-am", "a change")
        self.mine = self.git(self.dev, "rev-parse", "HEAD")

    def tearDown(self):
        self._td.cleanup()

    def git(self, cwd, *args):
        r = _run("git", *args, cwd=cwd, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def state(self, **kw):
        st = {"checks": "pass", "rules": True, "auto_ok": True,
              "auto": False, "state": "NONE", "branch": ""}
        st.update(kw)
        (self.d / "land.json").write_text(json.dumps(st))

    def land(self, **env):
        return _run("bash", str(self.dev / "scripts" / "land.sh"), cwd=self.d,
                    env=dict(self.env, **env))

    def calls(self):
        log = self.d / "gh.log"
        return ([json.loads(ln) for ln in log.read_text().splitlines()]
                if log.exists() else [])

    def origin_log(self):
        self.git(self.dev, "fetch", "-q", "origin")
        return self.git(self.dev, "log", "--format=%s", "origin/main").splitlines()

    def test_required_checks_without_up_to_date_rule_merge_here(self):
        self.state(strict=False)
        r = self.land(LAND_MOVE="view")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("main's rules do not require a branch up to date with "
                      "main", r.stdout)
        self.assertIn("main moved on while the checks ran", r.stderr)
        self.assertFalse([c for c in self.calls()
                          if c[:2] == ["pr", "merge"] and "--auto" in c])
        self.assertEqual(self.origin_log(), ["another pull request", "start"])
        self.assertEqual(self.git(self.dev, "rev-parse", "HEAD"), self.mine)

    def test_up_to_date_rule_keeps_auto_merge_and_refuses_a_moved_main(self):
        self.state(strict=True)
        r = self.land(LAND_MOVE="view")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("GitHub merges it (rebase) once its required checks pass",
                      r.stdout)
        self.assertIn("main moved on while the checks ran", r.stderr)
        # Main moved before every check passed: auto-merge was never
        # turned on (backlog TJ-0942), so nothing could merge later.
        self.assertFalse([c for c in self.calls()
                          if c[:2] == ["pr", "merge"] and "--auto" in c])
        self.assertEqual(self.origin_log(), ["another pull request", "start"])

    def test_an_unmoved_main_lands_either_way(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                self.state(strict=strict)
                r = self.land()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("⚠", r.stderr)
                self.assertEqual(self.origin_log(), ["a change", "start"])
                self.git(self.dev, "push", "-q", "-f", "origin",
                         f"{self.git(self.dev, 'rev-parse', 'HEAD~1')}:refs/heads/main")
                self.git(self.dev, "reset", "-q", "--hard", self.mine)

    def test_a_merge_onto_a_main_that_moved_at_the_last_moment_is_named(self):
        self.state(rules=False)
        r = self.land(LAND_MOVE="merge")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("⚠ main moved between the last look and the merge: the "
                      "landed commits do not sit on", r.stderr)
        self.assertIn("run the gate on main now (scripts/ci.sh)", r.stderr)
        self.assertEqual(self.origin_log(),
                         ["a change", "another pull request", "start"])


_FAILED_LAND = ("#!/usr/bin/env bash\n"
                "echo '✗ Check(s) failed: CI (https://github.com/taxjson/taxjson/pull/7)"
                " — the pull request is closed and its branch deleted; the commits"
                " stay on local main: fix, commit, and land again.' >&2\nexit 1\n")


class TestVersionCommitLandsLast(_ReleaseRepo):
    """TJ-0213: a check failed while --prepare landed the version commit;
    the advice was to fix in a new commit and land again, so main got
    [version commit, fix] in one push. GitHub runs tests.yml on a push's
    head only: the version commit had no run and the cut could not tag
    it. The advice now takes the version commit back first, and --prepare
    refuses a version commit that is not the last commit."""

    def fail_landing(self):
        land = self.dev / "scripts" / "land.sh"
        real = land.read_text()
        land.write_text(_FAILED_LAND)
        self.git(self.dev, "update-index", "--assume-unchanged", "scripts/land.sh")
        r = self.prepare()
        land.write_text(real)
        self.git(self.dev, "update-index", "--no-assume-unchanged", "scripts/land.sh")
        return r

    def fix(self):
        (self.dev / "fix.txt").write_text("the fix\n")
        self.git(self.dev, "add", "fix.txt")
        self.git(self.dev, "commit", "-q", "-m", "fix the check")

    def test_the_advice_takes_the_version_commit_back_first(self):
        r = self.fail_landing()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("A check that needs a fix: take the version commit back "
                      "first (git reset --keep HEAD~1), commit the fix and land "
                      "it, then scripts/release.sh v0.2.0 --prepare again",
                      r.stdout)
        self.assertNotIn("0.2.0", self.origin_version())
        # Followed: the version commit is made anew, last, and lands.
        self.git(self.dev, "reset", "-q", "--keep", "HEAD~1")
        self.fix()
        self.git(self.dev, "push", "-q", "origin", "main")     # "land it"
        vc = self.prepared()
        self.assertEqual(self.git(self.dev, "log", "-2", "--format=%s"),
                         "release v0.2.0\nfix the check")
        self.ci()
        r = self.release("v0.2.0")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git(self.dev, "rev-parse", "v0.2.0^{commit}"), vc)

    def test_a_fix_on_top_of_the_version_commit_is_refused(self):
        self.fail_landing()
        vc = self.git(self.dev, "rev-parse", "HEAD")
        self.fix()
        r = self.prepare()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"the version commit of v0.2.0 ({vc[:9]}) is not the last "
                      "commit on local main: 1 commit(s) follow it", r.stdout)
        self.assertIn(f"git rebase --onto {vc[:9]}~1 {vc[:9]}, then "
                      "scripts/release.sh v0.2.0 --prepare", r.stdout)
        self.assertNotIn("0.2.0", self.origin_version(), "nothing landed")
        # Followed: the version commit goes, and comes back last.
        self.git(self.dev, "rebase", "-q", "--onto", f"{vc}~1", vc)
        self.prepared()
        self.assertEqual(self.git(self.dev, "log", "-2", "--format=%s",
                                  "origin/main"),
                         "release v0.2.0\nfix the check")

    def test_a_version_commit_that_is_last_still_lands(self):
        self.fail_landing()
        vc = self.git(self.dev, "rev-parse", "HEAD")
        self.assertEqual(self.prepared(), vc, "a flaky check: landed as it is")


# Run by scripts/ci_no_extras.sh in a sandbox: what a test sees there.
_PLUGIN_PROBE = r'''
import importlib, os, subprocess, sys, tempfile, unittest
from importlib import metadata


class TestPluginHidden(unittest.TestCase):
    def test_no_fetcher_entry_point_or_distribution(self):
        names = [ep.name for ep in metadata.entry_points(group="taxjson.fetchers")]
        self.assertNotIn("taxjson-fetch", names)
        with self.assertRaises(metadata.PackageNotFoundError):
            metadata.distribution("taxjson-fetch")
        from taxjson.lib import fetchers
        self.assertNotIn("taxjson-fetch",
                         [f.name for f in fetchers.discover(refresh=True)[0]])

    def test_module_only_from_pythonpath(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("taxjson_fetch")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "taxjson_fetch"))
            with open(os.path.join(d, "taxjson_fetch", "__init__.py"), "w") as f:
                f.write("STAND_IN = 1\n")
            env = dict(os.environ,
                       PYTHONPATH=d + os.pathsep + os.environ["PYTHONPATH"])
            r = subprocess.run([sys.executable, "-c",
                                "import taxjson_fetch; print(taxjson_fetch.STAND_IN)"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "1", r.stderr)

    def test_the_cli_says_no_fetcher(self):
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            "fetch", "--list"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no fetcher is installed", r.stderr)
'''


@unittest.skipUnless(_BASH, "bash required")
class TestNoExtrasStageHidesThePlugin(unittest.TestCase):
    """TJ-0267: CI's matrix jobs run the suite before they install
    taxjson-fetch, the dev venv always has it, and a fetcher is found by
    its entry point: a test that assumed the plugin passed the gate and
    failed every hosted job. scripts/ci_no_extras.sh now hides the
    plugin too (no taxjson-fetch distribution, its module only from a
    PYTHONPATH entry) and runs every module that touches it."""

    def test_the_stage_hides_the_plugin(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "scripts").mkdir()
            for f in ("ci_no_extras.sh", "hide_optional.py",
                      "run_tests_parallel.py"):
                shutil.copy(REPO / "scripts" / f, d / "scripts" / f)
            shutil.copytree(REPO / "tests" / "_hermetic", d / "tests" / "_hermetic",
                            ignore=shutil.ignore_patterns("__pycache__"))
            (d / "tests" / "test_probe_plugin.py").write_text(_PLUGIN_PROBE)
            (d / "src").symlink_to(REPO / "src")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONPATH", "TAXJSON_TEST_JOBS")}
            env["TMPDIR"] = td
            r = _run("bash", str(d / "scripts" / "ci_no_extras.sh"), sys.executable,
                     "test_probe_plugin", cwd=td, env=env, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Ran 3 of 3 tests", r.stdout)

    def test_every_module_that_runs_fetch_is_in_the_stage(self):
        script = (REPO / "scripts" / "ci_no_extras.sh").read_text()
        rx = re.search(r'^PLUGIN_RE="(.*)"$', script, re.MULTILINE).group(1)
        rx = re.compile(rx.replace('\\"', '"').replace("\\\\", "\\"))
        picked = set()
        for f in (REPO / "tests").glob("test_*.py"):
            if any(rx.search(ln) and not re.search(r"\bgit\b", ln)
                   for ln in f.read_text().splitlines()):
                picked.add(f.stem)
        for mod in ("test_fix_backlog_errors", "test_fix_1027_build",
                    "test_fetch_dispatch", "test_style_run"):
            self.assertIn(mod, picked)
        self.assertNotIn("test_fix_release_safeguards", picked, "git fetch only")


# ---- TJ-0290: nothing the suite runs needs a newer Python than the floor --
# Interpreter options newer than Python 3.10 (pyproject's requires-python):
# -P (3.11) and these -X names. Python 3.10 refuses them ("Unknown option:
# -P", exit 2). src/taxjson/lib/dispatch.py `python_module_argv` takes -P
# only on 3.11+, by the running version.
_NEWER_X = ("frozen_modules", "int_max_str_digits", "perf", "perf_jit",
            "cpu_count", "presite", "gil")
_DISPATCH = "src/taxjson/lib/dispatch.py"


def _is_python(node):
    """An argv element that names a Python interpreter."""
    import ast
    if (isinstance(node, ast.Attribute) and node.attr == "executable"
            and isinstance(node.value, ast.Name) and node.value.id == "sys"):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.search(r"(^|/)python[0-9.]*$", node.value) is not None
    return isinstance(node, ast.Name) and "py" in node.id.lower()


def newer_python_argv(source, name="<source>", tree=None):
    """Each list / tuple display in SOURCE (or its parsed TREE) that runs
    Python with an option Python 3.10 refuses, as 'name:line'."""
    import ast
    out = []
    for node in ast.walk(tree or ast.parse(source, name)):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        el = node.elts
        consts = [e.value if isinstance(e, ast.Constant) else None for e in el]
        newer = "-P" in consts or any(
            c == "-X" and i + 1 < len(consts) and isinstance(consts[i + 1], str)
            and consts[i + 1].split("=")[0] in _NEWER_X
            for i, c in enumerate(consts))
        newer = newer or any(isinstance(c, str) and c.startswith("-X")
                             and c[2:].split("=")[0] in _NEWER_X for c in consts)
        if newer and any(_is_python(e) for e in el):
            out.append(f"{name}:{node.lineno}")
    return out


_SHELL_NEWER = re.compile(
    r"""(?:python[0-9.]*|\$\{?PY[A-Z_]*\}?)["']?\s+(?:-\S+\s+)*"""
    r"(?:-P\b|-X\s*(?:" + "|".join(_NEWER_X) + r")\b)")


def unguarded_tomllib(source, name="<source>", tree=None):
    """`import tomllib` (Python 3.11+) outside a try that catches
    ImportError, as 'name:line'."""
    import ast
    tree = tree or ast.parse(source, name)
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
                h.type is None or any(isinstance(t, ast.Name) and t.id in
                                      ("ImportError", "ModuleNotFoundError", "Exception")
                                      for t in (h.type.elts if isinstance(h.type, ast.Tuple)
                                                else [h.type]))
                for h in node.handlers):
            for stmt in node.body:
                guarded.update(id(n) for n in ast.walk(stmt))
    out = []
    for node in ast.walk(tree):
        mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) else [])
        if "tomllib" in mods and id(node) not in guarded:
            out.append(f"{name}:{node.lineno}")
    return out


class TestNothingNeedsANewerPythonThanTheFloor(unittest.TestCase):
    """TJ-0290: the gate ran the suite on the dev venv's Python (3.12)
    only, so two tests that started Python with -P (3.11+) passed it and
    failed CI's Python 3.10 job. What the floor version refuses is checked
    here, on any Python: an argv with a newer interpreter option outside
    dispatch.py, in the code, the tests, the plugin and the scripts and
    workflows; and an unguarded `import tomllib`."""

    @classmethod
    def setUpClass(cls):
        import ast
        files = [f for top in ("src", "tests", "packages")
                 for f in sorted((REPO / top).rglob("*.py"))]
        files.append(REPO / "scripts" / "run_tests_parallel.py")
        cls.trees = []
        for f in files:
            rel = f.relative_to(REPO).as_posix()
            cls.trees.append((rel, ast.parse(f.read_text(encoding="utf-8"), rel)))

    def test_the_checks_see_what_they_look_for(self):
        self.assertEqual(newer_python_argv(
            'import sys\nx = [sys.executable, "-P", "-m", "taxjson"]\n'), ["<source>:2"])
        self.assertEqual(newer_python_argv(
            'x = ("python3", "-X", "frozen_modules=off", "y.py")\n'), ["<source>:1"])
        self.assertEqual(newer_python_argv(
            'x = ["grep", "-P", "a+"]\ny = re.sub("x", "-P", z)\n'), [])
        self.assertTrue(_SHELL_NEWER.search('"$PY" -P -m taxjson'))
        self.assertTrue(_SHELL_NEWER.search("python3 -I -X perf x.py"))
        self.assertFalse(_SHELL_NEWER.search('grep -P "x" f; "$PY" -m pip'))
        self.assertEqual(unguarded_tomllib("import tomllib\n"), ["<source>:1"])
        self.assertEqual(unguarded_tomllib(
            "try:\n    import tomllib\nexcept ImportError:\n    import tomli as tomllib\n"), [])

    def test_no_argv_starts_python_with_a_newer_option(self):
        bad = []
        for rel, tree in self.trees:
            if rel != _DISPATCH:
                bad += newer_python_argv(None, rel, tree)
        self.assertEqual(bad, [], "start child Python through "
                         "taxjson.lib.dispatch.python_module_argv")
        disp = (REPO / _DISPATCH).read_text()
        self.assertIn("sys.version_info < (3, 11)", disp)

    def test_no_script_or_workflow_does(self):
        files = sorted((REPO / "scripts").rglob("*.sh")) + sorted(
            (REPO / "scripts" / "hooks").glob("*")) + sorted(REPO.glob("*.sh")) + sorted(
            (REPO / ".github" / "workflows").glob("*.yml")) + sorted(
            (REPO / "packages").rglob("*.sh"))
        self.assertTrue(files)
        bad = []
        for f in files:
            for n, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if not ln.lstrip().startswith("#") and _SHELL_NEWER.search(ln):
                    bad.append(f"{f.relative_to(REPO)}:{n}")
        self.assertEqual(bad, [])

    def test_tomllib_is_imported_with_its_fallback(self):
        bad = []
        for rel, tree in self.trees:
            bad += unguarded_tomllib(None, rel, tree)
        self.assertEqual(bad, [], "Python 3.10 has no tomllib: "
                         "from taxjson.lib.tomlcompat import tomllib")


# ---- check-pii.sh: --help, and where a hit is (TJ-0265, TJ-0215) --------
_REAL_ID = "U" + "7654321"           # an IB id shape that is not exempt
_BOOK = "48" + ",213.97"              # a figure "from the books" (synthetic)
_BARE = "48213" + ".97"


@unittest.skipUnless(_BASH, "bash required")
class TestCheckPiiHelp(unittest.TestCase):
    """TJ-0265: `check-pii.sh --help` took --help for a path ("path does
    not exist") and, its file list empty, said the figure-list guard was
    DISABLED."""

    def pii(self, *args, **env):
        e = dict(os.environ, **env)
        e.pop("TAXJSON_PII_AMOUNTS", None)
        e.update(env)
        return _run("bash", str(REPO / "scripts" / "check-pii.sh"), *args,
                    cwd=REPO, env=e)

    def test_help_prints_the_comment_block(self):
        for flag in ("--help", "-h"):
            r = self.pii(flag)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = r.stdout.splitlines()
            self.assertGreater(len(lines), 20)
            self.assertTrue(all(ln.startswith("#") for ln in lines), lines[:3])
            self.assertFalse(lines[0].startswith("#!"))
            text = r.stdout
            for mode in ("scripts/ci.sh", "--diff", "scripts/check-pii.sh PATH"):
                self.assertIn(mode, text)
            self.assertNotIn("path does not exist", r.stdout + r.stderr)

    def test_an_unknown_option_is_refused(self):
        r = self.pii("--bogus")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("unknown option --bogus", r.stderr)
        self.assertNotIn("DISABLED", r.stdout)

    def test_nothing_to_scan_leaves_the_figure_list_working(self):
        with tempfile.TemporaryDirectory() as td:
            figs = Path(td) / "pii-amounts"
            figs.write_text("# list\nsalt " + "ab" * 16 + "\n")
            deny = Path(td) / "denylist"
            deny.write_text("# test denylist\n")
            empty = Path(td) / "empty"
            empty.mkdir()
            env = {"TAXJSON_PII_AMOUNTS": str(figs), "TAXJSON_PII_DENYLIST": str(deny)}
            r = self.pii(str(empty), **env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("check-pii: clean", r.stdout)
            r = self.pii(str(Path(td) / "missing"), **env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("path does not exist", r.stdout)
            self.assertNotIn("DISABLED", r.stdout)
            self.assertIn("check-pii: 1 problem(s)", r.stdout)
            (empty / "--odd.txt").write_text("hello\n")
            r = self.pii("--", str(empty / "--odd.txt"), **env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestPiiNameAndFigureHitsAreLocated(_PiiSandbox):
    """TJ-0215: the file-NAME check masked every part of a path with a
    digit in it, and a figure-list hit printed its path as it was (an
    id-named folder too). Both now locate as every other hit does
    (hide_loc): only a part that would itself be refused is masked."""

    def _write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_name_hit_masks_only_the_id_part(self):
        self._write(f"data/{_REAL_ID}/stmt_2025.csv", "a,b\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME carries an account-id shape", r.stdout)
        self.assertIn("data/<masked>/stmt_2025.csv", r.stdout)
        self.assertNotIn(_REAL_ID, r.stdout)

    def test_figure_hit_is_located_like_other_hits(self):
        proj = self.tmp / "books" / "2025"
        (proj / "reports").mkdir(parents=True)
        (proj / "reports" / "margin.sum").write_text(f"GRAND TOTAL {_BOOK} CAD\n")
        figs = self.tmp / "figs" / "pii-amounts"
        self.env["TAXJSON_PII_AMOUNTS"] = str(figs)
        r = _run("bash", str(self.repo / "scripts" / "check-pii.sh"),
                 "--collect-amounts", str(proj), cwd=self.tmp, env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self._write(f"inputs/{_REAL_ID}/notes.txt", f"a\nb = {_BARE}\n")
        self._write("docs/2025_notes.md", f"total {_BOOK}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        out = r.stdout[r.stdout.index("matches a figure from your own books"):]
        self.assertIn("inputs/<masked>/notes.txt:2", out)
        self.assertIn("docs/2025_notes.md:1", out)
        for value in (_REAL_ID, _BOOK, _BARE):
            self.assertNotIn(value, r.stdout)
        r = self.scan("--message", stdin=f"subject\n\nTotal {_BOOK}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("message line 3", r.stdout)
        self.assertNotIn(_BOOK, r.stdout)


class TestCiUsageNamesEveryStage(unittest.TestCase):
    """TJ-0227: ci.sh's usage line for the default mode said "lint + full
    suite ... + fuzzers", leaving out the consistency, tax-rules, pii and
    fetch-plugin stages. It names every stage the default mode runs."""

    def test_default_usage_names_every_stage(self):
        ci = (REPO / "scripts" / "ci.sh").read_text()
        head = ci.split("\nset -u", 1)[0]
        block = head[head.index("#   scripts/ci.sh  "):]
        block = block[:block.index("#   scripts/ci.sh --")]
        text = " ".join(ln.lstrip("#").strip() for ln in block.splitlines())
        body = ci.split("\nset -u", 1)[1]
        body = body[body.index("# 1. Lint"):]           # the default mode's stages
        stages = {"lint"} | set(re.findall(r'^\s*stage "?([a-z-]+)', body, re.MULTILINE))
        stages -= {"mutation"}                          # --mutation only
        self.assertTrue({"consistency", "tax-rules", "pii", "suite",
                         "no-extras", "fetch-plugin", "fuzz"} <= stages, stages)
        for name in sorted(stages):
            self.assertIn(name, text, f"the default usage line names {name}")


if __name__ == "__main__":
    unittest.main()

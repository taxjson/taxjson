"""Backlog group `devtools`: the developer scripts (ci.sh, land.sh,
release.sh, check-pii.sh, github-ci.sh, dev-setup.sh) and their docs.

Every script runs in a throwaway folder: git remotes are local bare
repositories and `gh` / python are stubs on PATH, so nothing touches the
network or the real repository. Every value is synthetic.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_BASH = shutil.which("bash")
_GIT = shutil.which("git")


def _run(*args, cwd=None, env=None, timeout=120):
    return subprocess.run(list(args), cwd=cwd, env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=timeout)


def _git_env(home):
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam Synthetic",
                GIT_AUTHOR_EMAIL="sam@example.com",
                GIT_COMMITTER_NAME="Sam Synthetic",
                GIT_COMMITTER_EMAIL="sam@example.com",
                GIT_TERMINAL_PROMPT="0", HOME=str(home))


# python stand-in for scripts/ci.sh: `-c` runs the real python3 with the
# sandbox's src/ first (the gate-record checks import taxjson from it),
# `-m ruff --version` reports $RUFF_STUB_VERSION, everything else passes.
_CI_PY_STUB = r"""#!/usr/bin/env bash
case "${1:-}" in
  -c) PYTHONPATH="$PWD/src" exec python3 -c "$2" ;;
  -m) if [ "${2:-}" = ruff ] && [ "${3:-}" = --version ]; then
        echo "ruff ${RUFF_STUB_VERSION:-0.17.1}"; fi; exit 0 ;;
esac
exit 0
"""


@unittest.skipUnless(_BASH and _GIT and shutil.which("python3"),
                     "bash, git and python3 required")
class _CiSandbox(unittest.TestCase):
    """A committed git tree holding the real scripts/ci.sh and
    gate-record.sh, every other stage a stub that passes, and a python
    stub as $PYTHON."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.home = self.d / "home"
        self.home.mkdir()
        (self.d / "tmp").mkdir()
        self.repo = self.d / "repo"
        s = self.repo / "scripts"
        s.mkdir(parents=True)
        for f in ("ci.sh", "gate-record.sh"):
            shutil.copy(REPO / "scripts" / f, s / f)
        for f in ("check-consistency.sh", "check-pii.sh", "ci_no_extras.sh",
                  "check_tax_rules.py", "run_tests_parallel.py"):
            (s / f).write_text("exit 0\n")
        (self.repo / "src" / "taxjson").mkdir(parents=True)
        (self.repo / "src" / "taxjson" / "__init__.py").write_text("")
        for d in ("tests", "packages/taxjson-fetch/tests"):
            (self.repo / d).mkdir(parents=True)
            (self.repo / d / "README").write_text("stub\n")
        (self.repo / "requirements").mkdir()
        (self.repo / "requirements" / "dev.txt").write_text(
            "# lock\nruff==0.17.1 \\\n    --hash=sha256:" + "0" * 64 + "\n")
        (self.repo / ".gitignore").write_text(".ci/\n__pycache__/\n")
        self.py = self.d / "python-stub"
        self.py.write_text(_CI_PY_STUB)
        self.py.chmod(0o755)
        self.env = dict(_git_env(self.home), TMPDIR=str(self.d / "tmp"),
                        TAXJSON_GATE_CACHE=str(self.d / "gatecache"),
                        PYTHON=str(self.py))
        for k in ("PYTHONPATH", "TAXJSON_TEST_JOBS", "RUFF_STUB_VERSION"):
            self.env.pop(k, None)
        for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                     ("commit", "-q", "-m", "start")):
            r = _run("git", *args, cwd=self.repo, env=self.env)
            self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self._td.cleanup()

    def ci(self, *args, **env):
        return _run("bash", str(self.repo / "scripts" / "ci.sh"), *args,
                    cwd=self.d, env=dict(self.env, **env))


class TestCiResultIsTheLastLine(_CiSandbox):
    """TJ-0131: a full gate's last line was the gate-record note, not the
    PASS line AGENTS.md says to read."""

    def test_full_pass_ends_on_the_result_line(self):
        r = self.ci()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertRegex(lines[-1], r" default PASS \d+s$")
        rec = [ln for ln in lines if "gate record: " in ln]
        self.assertEqual(len(rec), 1, r.stdout)
        self.assertTrue(Path(rec[0].split("gate record: ", 1)[1]).is_file(), rec)
        self.assertLess(lines.index(rec[0]), len(lines) - 1)

    def test_quick_and_a_failure_end_on_it_too(self):
        r = self.ci("--quick")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.splitlines()[-1], r" quick PASS \d+s$")
        (self.repo / "scripts" / "check-pii.sh").write_text("exit 1\n")
        r = self.ci()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout.splitlines()[-1], r" default FAIL\(pii\) \d+s$")


class TestCiNeedsItsPython(_CiSandbox):
    """TJ-0151: in a fresh clone (no venv) every stage failed, the lint
    stage saying ruff was not installed; it now says so up front."""

    def test_no_venv_is_named_before_any_stage(self):
        self.env.pop("PYTHON")
        r = self.ci("--quick")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no venv at ./venv", r.stderr)
        self.assertIn("scripts/dev-setup.sh", r.stderr)
        self.assertIn("PYTHONPATH=$PWD/src PYTHON=", r.stderr)
        self.assertNotIn("== lint ==", r.stdout)
        self.assertNotIn("ruff is not installed", r.stdout + r.stderr)
        self.assertFalse((self.repo / ".ci" / "history.log").exists())

    def test_a_python_that_is_not_there(self):
        r = self.ci("--quick", PYTHON=str(self.d / "nowhere" / "python3"))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("PYTHON=" + str(self.d / "nowhere" / "python3")
                      + " cannot be run", r.stderr)
        self.assertNotIn("== lint ==", r.stdout)

    def test_agents_md_gives_the_fresh_clone_command(self):
        text = (REPO / "AGENTS.md").read_text()
        self.assertIn("PYTHONPATH=$PWD/src PYTHON=<dev clone>/venv/bin/python3 "
                      "scripts/ci.sh", text)


def _lint_job(wf):
    job = wf[wf.index("\n  lint:\n"):]
    return job[:job.index("\n  gates:\n")]


class TestOneRuffVersion(_CiSandbox):
    """TJ-0152: CI pinned one ruff, the dev lock another, and
    scripts/dev-setup.sh installed whatever was newest. All three now
    take the dev lock's (requirements/dev.txt)."""

    def test_ci_and_dev_setup_install_the_dev_lock(self):
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        job = _lint_job(wf)
        self.assertIn("pip install --require-hashes --only-binary :all: "
                      "-r requirements/dev.txt", job)
        self.assertNotRegex(job, r"ruff==")
        setup = (REPO / "scripts" / "dev-setup.sh").read_text()
        code = [ln for ln in setup.splitlines() if not ln.lstrip().startswith("#")]
        lock = [i for i, ln in enumerate(code)
                if "pip install --require-hashes" in ln and "-r requirements/dev.txt" in ln]
        editable = [i for i, ln in enumerate(code) if 'pip install -e ".[fx,dev]"' in ln]
        self.assertEqual(len(lock), 1, code)
        self.assertEqual(len(editable), 1, code)
        self.assertLess(lock[0], editable[0])
        pins = re.findall(r"(?m)^ruff==(\S+) \\$",
                          (REPO / "requirements" / "dev.txt").read_text())
        self.assertEqual(len(pins), 1, pins)

    def test_ci_sh_names_another_ruff(self):
        r = self.ci("--quick", RUFF_STUB_VERSION="0.17.1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("lint: note", r.stdout)
        r = self.ci("--quick", RUFF_STUB_VERSION="0.16.8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("lint: note: ruff 0.16.8 here, 0.17.1 in "
                      "requirements/dev.txt (what CI lints with): "
                      "scripts/dev-setup.sh installs it", r.stdout)


class TestWorkflowsAreValidYaml(unittest.TestCase):
    """TJ-0266: the dev lock's `pip install … --only-binary :all: -r …`
    went in as a plain `run:` value, whose ": " YAML reads as a mapping:
    GitHub refused the whole tests.yml and no job ran. The gate never
    parses the workflows, so this checks them: a plain (unquoted, not a
    block) `run:` value has no ": " and does not end in ":"; and, where
    PyYAML is installed, every workflow file loads."""

    WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))

    def test_plain_run_values_have_no_mapping_colon(self):
        self.assertTrue(self.WORKFLOWS)
        bad = []
        for wf in self.WORKFLOWS:
            for n, ln in enumerate(wf.read_text().splitlines(), 1):
                m = re.match(r"^\s*(?:- )?run:\s+(.*)$", ln)
                if not m or m.group(1)[:1] in ("'", '"', "|", ">"):
                    continue
                value = re.sub(r"\s+#.*$", "", m.group(1))
                if ": " in value or value.endswith(":"):
                    bad.append(f"{wf.name}:{n}: {ln.strip()}")
        self.assertEqual(bad, [], "quote these run: values")

    def test_workflows_load(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for wf in self.WORKFLOWS:
            with self.subTest(wf.name):
                self.assertIsInstance(yaml.safe_load(wf.read_text()), dict)


@unittest.skipUnless(_BASH, "bash required")
class TestHelpShowsTheCommentBlock(unittest.TestCase):
    """TJ-0169: --help printed shell code past the comment block, and
    ci.sh's --quick line said lint + suite only."""

    def help_of(self, script, *args):
        r = _run("bash", str(REPO / "scripts" / script), *args, cwd=REPO)
        return r, r.stdout.splitlines()

    def test_every_line_is_the_comment(self):
        for script, args, rc in (("ci.sh", ("--help",), 0),
                                 ("land.sh", ("--help",), 0),
                                 ("gate-record.sh", (), 2)):
            r, lines = self.help_of(script, *args)
            self.assertEqual(r.returncode, rc, script + r.stderr)
            self.assertGreater(len(lines), 10, script)
            for ln in lines:
                self.assertTrue(ln.startswith("#"), f"{script}: {ln!r}")
            self.assertFalse(lines[0].startswith("#!"), script)

    def test_quick_skips_only_the_fuzzers(self):
        _, lines = self.help_of("ci.sh", "--help")
        quick = [ln for ln in lines if "--quick" in ln]
        self.assertEqual(len(quick), 1, quick)
        self.assertIn("every stage but the fuzzers", quick[0])
        contributing = (REPO / "CONTRIBUTING.md").read_text()
        self.assertNotIn("--quick    # lint + suite only", contributing)


# gh stand-in for scripts/github-ci.sh, answering as through its --jq: the
# tests.yml runs query with "4242 <status> <conclusion>" ($CI_FIRST), and
# each poll of actions/runs/4242 with the next line of $CI_POLLS ("fail":
# an HTTP error); the last line repeats. Every call is counted in
# $CI_POLLS.n.
_CI_GH = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[:1] != ["api"]:
    sys.exit(0)
path = args[1]
if "actions/workflows/tests.yml/runs" in path:
    print(os.environ["CI_FIRST"]); sys.exit(0)
if path.endswith("/actions/runs/4242"):
    polls = os.environ["CI_POLLS"]
    lines = open(polls).read().split("\n")
    lines = [ln for ln in lines if ln]
    try:
        n = int(open(polls + ".n").read())
    except OSError:
        n = 0
    open(polls + ".n", "w").write(str(n + 1))
    ans = lines[min(n, len(lines) - 1)]
    if ans == "fail":
        sys.stderr.write("gh: Server Error (HTTP 502)\n"); sys.exit(1)
    print(ans); sys.exit(0)
sys.exit(1)
"""

_SHA = "ab" * 20


@unittest.skipUnless(_BASH and shutil.which("python3"), "bash and python3 required")
class TestCiWaitIsBounded(unittest.TestCase):
    """TJ-0153: github-ci.sh polled a queued or running run with no time
    limit (a run queued for an offline self-hosted runner held the release
    cut and promote for ever), and one failed poll ended the wait."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        (self.d / "repo" / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts" / "github-ci.sh", self.d / "repo" / "scripts")
        (self.d / "bin").mkdir()
        gh = self.d / "bin" / "gh"
        gh.write_text(_CI_GH)
        gh.chmod(0o755)
        self.polls = self.d / "polls"
        self.env = dict(os.environ, PATH=f"{self.d / 'bin'}:{os.environ['PATH']}",
                        CI_POLLS=str(self.polls), CI_FIRST="4242 queued null",
                        TAXJSON_CI_POLL_SECS="0")
        self.env.pop("TAXJSON_SLUG", None)

    def tearDown(self):
        self._td.cleanup()

    def wait(self, *polls, **env):
        self.polls.write_text("\n".join(polls) + "\n")
        (self.d / "polls.n").unlink(missing_ok=True)
        return _run("bash", str(self.d / "repo" / "scripts" / "github-ci.sh"),
                    _SHA, "the version commit of v0.2.0", "--slug", "owner/dev",
                    cwd=self.d, env=dict(self.env, **env), timeout=30)

    def polled(self):
        return int((self.d / "polls.n").read_text())

    def test_a_run_still_queued_at_the_limit_stops_the_wait(self):
        r = self.wait("queued null", TAXJSON_CI_WAIT_MINS="0")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("✗ CI for the version commit of v0.2.0 is still queued "
                      "after 0 min (TAXJSON_CI_WAIT_MINS): "
                      "https://github.com/owner/dev/actions/runs/4242", r.stderr)
        self.assertEqual(self.polled(), 1)

    def test_a_run_that_finishes_in_time_is_judged(self):
        r = self.wait("in_progress null", "in_progress null", "completed success")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✓ CI passed on the version commit of v0.2.0", r.stdout)
        r = self.wait("completed failure", TAXJSON_CI_WAIT_MINS="0")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("CI failed on the version commit of v0.2.0 (failure)", r.stderr)

    def test_a_failed_poll_is_tried_again(self):
        r = self.wait("fail", "fail", "completed success")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.polled(), 3)
        r = self.wait("fail")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("5 tries in a row failed", r.stderr)
        self.assertEqual(self.polled(), 5)

    def test_bad_limit_is_refused(self):
        r = self.wait("queued null", TAXJSON_CI_WAIT_MINS="soon")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("TAXJSON_CI_WAIT_MINS takes whole minutes", r.stderr)


# A gh wrapper around test_fix_release_hardening's land.sh stub: the first
# `gh pr view` (land.sh's first poll) first pushes another commit onto
# origin's main, as a pull request merged meanwhile would.
_MOVING_MAIN_GH = r"""#!/usr/bin/env python3
import os, subprocess, sys, tempfile
args = sys.argv[1:]
flag = os.environ["LAND_MOVE_FLAG"]
if args[:2] == ["pr", "view"] and os.environ.get("LAND_MOVE") and not os.path.exists(flag):
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
class TestLandOnlyOntoTheGatedBase(unittest.TestCase):
    """TJ-0132: land.sh checked that main had not moved only before it
    pushed; a pull request merged during its wait made GitHub's rebase
    merge replay the commits onto a base no gate ran on."""

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
        for k in ("TAXJSON_LAND_DIRECT", "TAXJSON_SLUG", "TAXJSON_PII_AMOUNTS"):
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
        (self.d / "land.json").write_text(
            '{"checks": "pass", "rules": false, "auto_ok": false, '
            '"auto": false, "state": "NONE", "branch": ""}')
        (self.dev / "f.txt").write_text("a change\n")
        self.git(self.dev, "commit", "-q", "-am", "a change")
        self.mine = self.git(self.dev, "rev-parse", "HEAD")

    def tearDown(self):
        self._td.cleanup()

    def git(self, cwd, *args):
        r = _run("git", *args, cwd=cwd, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def land(self, **env):
        return _run("bash", str(self.dev / "scripts" / "land.sh"), cwd=self.d,
                    env=dict(self.env, **env))

    def origin_log(self):
        return self.git(self.dev, "log", "--format=%s", "origin/main")

    def test_a_main_that_moved_meanwhile_is_not_merged_onto(self):
        r = self.land(LAND_MOVE="1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("main moved on while the checks ran", r.stderr)
        self.assertIn("the pull request is closed and its branch deleted; the "
                      "commits stay on local main: git pull --rebase, run the "
                      "gate again, and land again", r.stderr)
        self.git(self.dev, "fetch", "-q", "origin")
        self.assertEqual(self.origin_log().splitlines(),
                         ["another pull request", "start"])
        calls = [json.loads(ln) for ln in (self.d / "gh.log").read_text().splitlines()]
        self.assertFalse([c for c in calls if c[:2] == ["pr", "merge"]], calls)
        close = [c for c in calls if c[:2] == ["pr", "close"]]
        self.assertEqual(len(close), 1, calls)
        self.assertIn("--delete-branch", close[0])
        self.assertEqual(self.git(self.dev, "rev-parse", "HEAD"), self.mine)

    def test_an_unmoved_main_still_lands(self):
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.origin_log().splitlines(), ["a change", "start"])

    def test_releasing_md_says_why_it_rebase_merges(self):
        """TJ-0002: the reasons, the dev side's against the public side's
        squash, and the base check that makes the first one true."""
        text = (REPO / "docs" / "releasing.md").read_text()
        why = text[text.index("**Why a rebase merge**"):]
        why = why[:why.index("\nNothing enforces this")]
        for part in ("merges only while it still has not",
                     "CI on the version commit itself",
                     '`mode = "squash"` in `publish.toml`',
                     "the release cut tags only a version commit"):
            self.assertIn(part, why)
        self.assertIn('mode = "squash"', (REPO / "publish.toml").read_text())


@unittest.skipUnless(_BASH, "bash required")
class TestPrepareLeavesTheKitChecksToItsPullRequest(unittest.TestCase):
    """TJ-0001 (decided: not wired in): release.sh --prepare runs no
    scripts/kit_checks.py because it lands the version commit through
    land.sh, which waits for the kit-checks job on that pull request.
    These hold the premises of that decision."""

    def test_land_sh_waits_for_the_kit_checks_job(self):
        # land.sh's own `expected` list: every workflow each pull request
        # triggers. kit-checks must be in it (no paths filter).
        land = (REPO / "scripts" / "land.sh").read_text()
        body = land[land.index("expected() {"):]
        body = body[:body.index("\n}\n") + 3]
        r = _run("bash", "-c", body + "\nexpected", cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kit-checks", r.stdout.splitlines())

    def test_prepare_publishes_through_land_sh_and_waits(self):
        release = (REPO / "scripts" / "release.sh").read_text()
        fn = release[release.index("publish_main() {"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("bash scripts/land.sh", fn)
        self.assertNotIn("--no-wait", fn)
        self.assertNotIn("kit_checks.py", fn)
        doc = (REPO / "docs" / "releasing.md").read_text()
        self.assertIn("`scripts/release.sh --prepare` does not run `kit_checks.py` "
                      "itself", doc.replace("\n", " "))


# ---- TJ-0150: check-pii.sh shows where a hit is, never what -------------
from test_check_pii import _NAME, _Sandbox

_MAIL = "quinn.mail" + "@" + "corp" + ".io"      # an address not on the allowlist
_REAL_ID = "U" + "7654321"                         # an IB id shape that is not exempt


class TestPiiHitShowsFileAndLine(_Sandbox):
    """The file and line of a hit are shown; its content is masked, and so
    is any path part that would itself be refused (TJ-0150)."""

    def _write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _assert_private_values_absent(self, out):
        for value in (_MAIL, _REAL_ID, _NAME.lower(), "corp.io"):
            self.assertNotIn(value, out.lower())

    def test_tree_hit_names_the_file_and_line(self):
        self._write("docs/limits.md", "line\n" * 30 + f"contact {_MAIL}\n")
        self._write("tests/data2/file_2025.txt", f"contact {_MAIL}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("docs/limits.md:31:contact <masked>", r.stdout)
        self.assertIn("tests/data2/file_2025.txt:1:contact <masked>", r.stdout)
        self._assert_private_values_absent(r.stdout)

    def test_a_path_part_that_is_itself_refused_stays_masked(self):
        self._write(f"inputs/{_REAL_ID}/notes.txt", f"contact {_MAIL}\n")
        self._write(f"people/{_NAME.lower()}/notes.txt", f"contact {_MAIL}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("inputs/<masked>/notes.txt:1:contact <masked>", r.stdout)
        self.assertIn("people/<masked>/notes.txt:1:contact <masked>", r.stdout)
        self._assert_private_values_absent(r.stdout)

    def test_denylist_hit_shows_the_line_not_the_content(self):
        self._write("docs/people.md", f"a\nb\nsee {_NAME} here\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("docs/people.md:3: <content hidden>", r.stdout)
        self._assert_private_values_absent(r.stdout)

    def test_diff_hit_names_the_new_file_and_its_line(self):
        lines = [f"line {i}" for i in range(1, 13)]
        self._write("docs/a2.md", "\n".join(lines) + "\n")
        self._write("CHANGELOG.md", "# Changelog\n\n- one\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        base = self.git("rev-parse", "HEAD").strip()
        lines[4] = "five, changed"
        lines[10] = f"contact {_MAIL}"
        self._write("docs/a2.md", "\n".join(lines) + "\n")
        amount = "1" + ",234,567" + ".89"
        self._write("CHANGELOG.md", f"# Changelog\n\n- one\n- Total {amount} paid\n")
        self._write("docs/new.md", f"new\ncontact {_MAIL}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")
        r = self.scan("--diff", stdin=self.git("diff", base, "HEAD"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("docs/a2.md:11:contact <masked>", r.stdout)
        self.assertIn("docs/new.md:2:contact <masked>", r.stdout)
        self.assertIn("CHANGELOG.md:4:- Total <masked>", r.stdout)
        self.assertNotIn(amount, r.stdout)
        self._assert_private_values_absent(r.stdout)

    def test_message_hit_keeps_its_line_number(self):
        r = self.scan("--message", stdin=f"subject\n\ncontact {_MAIL}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("text line 3: contact <masked>", r.stdout)
        self._assert_private_values_absent(r.stdout)

    def test_account_column_hit_names_the_file_and_row(self):
        acct = "5550" + "8765"
        self._write("data/acct.csv", f"Date,Account,Amount\n2025-01-02,{acct},5\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("data/acct.csv:2:", r.stdout)
        self.assertNotIn(acct, r.stdout)


if __name__ == "__main__":
    unittest.main()

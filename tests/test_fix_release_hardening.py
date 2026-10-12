"""Release and CI hardening (v0.28.3, owner-approved 2026-10-10).

- scripts/land.sh: main is published through a pull request, rebase-merged
  once its checks pass (stub gh; the "GitHub" here is a local bare repo).
- Every workflow pins its actions by SHA and its runners by image.
- The required checks of main's ruleset (the JSON in docs/releasing.md)
  are exactly the tests.yml jobs that run on every pull request.
- gitleaks: no word-list allowlist; scripts/gitleaks-regression.sh proves
  ordinary secrets are reported whatever they are called.
- The daily privacy watch (read-only) and check-public.sh --days.

Every value is synthetic; nothing reaches the network.
"""
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from test_fix_release_safeguards import _Sandbox, _public_api

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
_GITHUB_ACTIONS_APP = 15368


def _jobs(text):
    """{job id: its block} of a workflow (two-space-indented job ids)."""
    jobs = text[text.index("\njobs:\n"):]
    out = {}
    for b in re.split(r"\n  (?=[a-z][a-z0-9-]*:\n)", jobs)[1:]:
        out[b.split(":", 1)[0]] = b
    return out


def _runs_on_every_pull_request(block):
    m = re.search(r"\n    if: (.+)", block)
    return m is None or "github.event_name == 'pull_request'" in m.group(1)


def _check_names(block, job_id):
    """The check-run names a job reports (its matrix expanded)."""
    m = re.search(r"\n    name: (.+)", block)
    name = m.group(1).strip() if m else job_id
    names = [name]
    for key in ("os", "python-version"):
        mm = re.search(r"\n        %s: \[(.*)\]" % re.escape(key), block)
        if not mm:
            continue
        vals = [v.strip().strip('"\'') for v in mm.group(1).split(",")]
        names = [n.replace("${{ matrix.%s }}" % key, v)
                 for n in names for v in vals]
    return names


def _ruleset():
    doc = (REPO / "docs" / "releasing.md").read_text()
    m = re.search(r"<!-- ruleset: main -->\n```json\n(.*?)\n```", doc, re.S)
    assert m, "docs/releasing.md has no ruleset block"
    return json.loads(m.group(1))


class TestWorkflowPins(unittest.TestCase):
    """Actions by full commit SHA with the release beside it; runners by an
    explicit image, never a moving *-latest label; read-only tokens."""

    def test_every_workflow(self):
        self.assertGreaterEqual(len(WORKFLOWS), 2)
        for wf in WORKFLOWS:
            text = wf.read_text()
            with self.subTest(workflow=wf.name):
                uses = re.findall(r"uses:\s*(\S+)(.*)", text)
                self.assertTrue(uses)
                for ref, rest in uses:
                    self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$",
                                     ref)
                    self.assertRegex(rest, r"^\s+#\s*v\d+(\.\d+)*\s*$", ref)
                labels = re.findall(r"runs-on:\s*(.+)", text)
                labels += [v for m in re.findall(r"\n\s+os: \[(.*)\]", text)
                           for v in m.split(",")]
                self.assertTrue(labels)
                for lab in labels:
                    lab = lab.strip().strip('"\'')
                    self.assertNotRegex(lab, r"-latest$", lab)
                    if not lab.startswith("${{"):
                        self.assertRegex(lab, r"^ubuntu-\d\d\.\d\d$", lab)
                self.assertRegex(text, r"\npermissions:\n")
                self.assertNotRegex(text, r":\s*write\b")
                for job, block in _jobs(text).items():
                    self.assertRegex(block, r"\n    timeout-minutes: \d+\n",
                                     job)
                    for step in block.split("\n      - "):
                        if "uses: actions/checkout@" in step:
                            self.assertIn("persist-credentials: false", step,
                                          job)

    def test_actions_run_on_node24_releases(self):
        # The releases whose action.yml says `using: node24` (checked
        # against the actions' own tags with gh api, 2026-10-10).
        text = "".join(wf.read_text() for wf in WORKFLOWS)
        for ref in re.findall(r"uses:\s*(actions/checkout@\S+)", text):
            self.assertEqual(ref, "actions/checkout@"
                             "3d3c42e5aac5ba805825da76410c181273ba90b1")
        for ref in re.findall(r"uses:\s*(actions/setup-python@\S+)", text):
            self.assertEqual(ref, "actions/setup-python@"
                             "5fda3b95a4ea91299a34e894583c3862153e4b97")


class TestRequiredChecks(unittest.TestCase):
    """main's ruleset (docs/releasing.md) requires exactly the tests.yml
    checks every pull request reports — none that a pull request skips
    (it would stay pending forever) and none left out."""

    def test_ruleset_names_every_pull_request_check(self):
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        on = wf[wf.index("\non:\n"):wf.index("\npermissions:")]
        self.assertIn("\n  pull_request:\n", on)
        self.assertNotRegex(on, r"\n\s+paths(-ignore)?:",
                            "a path filter can skip a required check")
        want = set()
        for job, block in _jobs(wf).items():
            if _runs_on_every_pull_request(block):
                want.update(_check_names(block, job))
        self.assertIn("ubuntu-24.04 / Python 3.13", want)
        self.assertIn("pii / new commits", want)
        self.assertIn("secrets (gitleaks)", want)
        self.assertNotIn("ubuntu-24.04 / Python 3.12 / nightly fuzz", want)
        rs = _ruleset()
        rules = {r["type"]: r.get("parameters") for r in rs["rules"]}
        req = rules["required_status_checks"]["required_status_checks"]
        self.assertEqual({c["context"] for c in req}, want)
        self.assertEqual({c["integration_id"] for c in req},
                         {_GITHUB_ACTIONS_APP})
        self.assertEqual(rules["pull_request"]["allowed_merge_methods"],
                         ["rebase"])
        for t in ("required_linear_history", "deletion", "non_fast_forward"):
            self.assertIn(t, rules)
        self.assertEqual(rs["bypass_actors"], [])
        self.assertEqual(rs["target"], "branch")
        self.assertEqual(rs["conditions"]["ref_name"]["include"],
                         ["~DEFAULT_BRANCH"])

    def test_pii_commit_scan_runs_on_pushes_to_main_too(self):
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        job = _jobs(wf)["pr-commits"]
        self.assertIn("if: github.event_name == 'pull_request' || "
                      "github.event_name == 'push'", job)
        self.assertIn("BASE_SHA: ${{ github.event.pull_request.base.sha || "
                      "github.event.before }}", job)
        self.assertIn('git show "$BASE_SHA:scripts/check-pii.sh"', job)
        self.assertIn('range="$HEAD_SHA^!"', job)


class TestGitleaks(unittest.TestCase):
    def test_no_word_list_allowlist(self):
        cfg = (REPO / ".gitleaks.toml").read_text()
        body = "\n".join(ln for ln in cfg.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertIn("useDefault = true", body)
        self.assertNotIn("[[allowlists]]", body)
        self.assertNotRegex(body.lower(), r"fake|example|synthetic|dummy|"
                                          r"placeholder|made-?up")
        # An exception, if ever, is an exact value for one rule in one file.
        for rx in re.findall(r"regexes = \[(.*?)\]", body, re.S):
            for one in re.findall(r"'''(.*?)'''", rx):
                self.assertTrue(one.startswith("^") and one.endswith("$"), one)

    def test_ci_runs_the_regression_from_the_base(self):
        wf = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        job = _jobs(wf)["secrets"]
        self.assertIn('git show "$BASE:.gitleaks.toml"', job)
        self.assertIn('git show "$BASE:scripts/gitleaks-regression.sh"', job)
        self.assertIn('bash "$reg" "$RUNNER_TEMP/gitleaks" '
                      '"$GITHUB_WORKSPACE/.gitleaks.toml"', job)
        for run in re.findall(r"run: \|\n(.*?)(?=\n      - |\Z)", job, re.S):
            self.assertNotIn("${{", run)          # event data only via env

    def _binary(self):
        b = os.environ.get("TAXJSON_GITLEAKS") or shutil.which("gitleaks")
        if not b:
            self.skipTest("no gitleaks binary (TAXJSON_GITLEAKS or PATH)")
        return b

    def test_regression_reports_ordinary_secrets(self):
        b = self._binary()
        reg = REPO / "scripts" / "gitleaks-regression.sh"
        r = subprocess.run(["bash", str(reg), b], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The old word-list config hides them: the regression says so.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "old.toml"
            cfg.write_text(
                "[extend]\nuseDefault = true\n[[allowlists]]\n"
                "regexTarget = \"match\"\nregexes = ['''(?i)fake|example|"
                "synthetic|placeholder|dummy|made-?up|0123456789|1234567890"
                "''', '''aB3dE5''']\n")
            r = subprocess.run(["bash", str(reg), b, str(cfg)],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not reported: scripts/fake-env.sh", r.stdout)

    def test_tracked_files_pass(self):
        b = self._binary()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # Tracked files only (a compiled __pycache__ holds the secret
            # shapes the tests assemble at run time).
            arc = subprocess.run(["git", "-C", str(REPO), "archive",
                                  "HEAD", "tests", "scripts", "src"],
                                 capture_output=True)
            if arc.returncode != 0:
                self.skipTest("not a git checkout")
            subprocess.run(["tar", "-x", "-C", td], input=arc.stdout,
                           check=True)
            r = subprocess.run([b, "dir", td, "--config",
                                str(REPO / ".gitleaks.toml"), "--redact",
                                "--no-banner", "--exit-code", "1"],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestPrivacyWatch(unittest.TestCase):
    def test_workflow_is_daily_read_only_and_secret_free(self):
        wf = (REPO / ".github" / "workflows" / "privacy-watch.yml").read_text()
        self.assertRegex(wf, r"\n  schedule:\n    - cron: \"[^\"]+\"")
        self.assertIn("\n  workflow_dispatch:\n", wf)
        perms = wf[wf.index("\npermissions:\n"):wf.index("\njobs:")]
        self.assertEqual(sorted(re.findall(r"\n  ([a-z-]+): (\w+)", perms)),
                         [("contents", "read"), ("issues", "read"),
                          ("pull-requests", "read")])
        self.assertNotIn("secrets.", wf)
        self.assertIn("GH_TOKEN: ${{ github.token }}", wf)
        self.assertIn("run: bash scripts/check-public.sh --days 2", wf)


class TestCheckPublicDays(_Sandbox):
    """check-public.sh --days N: what changed lately, plus main's recent
    commit messages (scanned as messages)."""

    def setUp(self):
        super().setUp()
        self.env["TAXJSON_SLUG"] = "taxjson/taxjson"

    def run_check(self, *args, commits=(), **public):
        table = _public_api(**public)
        table["repos/taxjson/taxjson/commits?"] = list(commits)
        f = self.d / "api.json"
        f.write_text(json.dumps(table))
        self.env["GH_STUB_API"] = str(f)
        return subprocess.run(["bash", str(REPO / "scripts" /
                                           "check-public.sh"), *args],
                              capture_output=True, text=True, env=self.env)

    def test_recent_items_and_commit_messages(self):
        amount = "7" + ",654.32"
        r = self.run_check(
            "--days", "2",
            commits=[{"sha": "a" * 40, "commit": {"message": "Fix a typo"}},
                     {"sha": "b" * 40,
                      "commit": {"message": f"Totals now {amount}"}}],
            commit_comments=[{"html_url": "https://x/c/1", "body": "old",
                              "updated_at": "2000-01-01T00:00:00Z"}])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("!! commits of taxjson/taxjson:", r.stdout)
        self.assertIn("in commit bbbbbbbbbbbb:", r.stdout)
        self.assertNotIn("commit aaaaaaaaaaaa", r.stdout)
        self.assertNotIn(amount, r.stdout + r.stderr)
        paths = [c[2] for c in self.gh_calls() if c[0] == "api"]
        since = [p for p in paths if "since=" in p]
        self.assertEqual(sorted(p.split("?")[0].rsplit("/", 1)[-1]
                                for p in since),
                         ["comments", "comments", "commits", "issues"])
        self.assertTrue(any(p.startswith("repos/taxjson/taxjson/releases?")
                            and "since" not in p for p in paths))
        r = self.run_check("--days", "2", commits=[
            {"sha": "a" * 40, "commit": {"message": "Fix a typo"}}],
            commit_comments=[{"html_url": "https://x/c/1", "body": "old",
                              "updated_at": "2000-01-01T00:00:00Z"}])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 commits", r.stdout)
        self.assertIn("0 commit-comments", r.stdout)   # older than --days

    def test_without_days_no_commits_and_bad_days(self):
        r = self.run_check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("commits,", r.stdout + ",")
        self.assertFalse([c for c in self.gh_calls()
                          if c[0] == "api" and "/commits?" in c[2]])
        r = self.run_check("--days", "soon")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--days takes a whole number", r.stderr)

    def test_a_token_needs_no_login(self):
        self.env["GH_STUB_AUTH"] = "fail"
        self.assertEqual(self.run_check().returncode, 2)
        self.env["GH_TOKEN"] = "stub"
        r = self.run_check("--days", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# gh stand-in for land.sh's pull-request path. State in $LAND_STATE (JSON):
# checks ("pass" | "fail" | "pending" | "none"), rules (required checks on
# main?), auto_ok (may auto-merge be turned on?), auto, state, branch. A
# merge rebases the branch's commits onto origin's main with GitHub as the
# committer (new ids), as GitHub's rebase merge does.
_LAND_GH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile
args = sys.argv[1:]
with open(os.environ["GH_STUB_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
sp = os.environ["LAND_STATE"]
st = json.load(open(sp))
def save():
    json.dump(st, open(sp, "w"))
def merge():
    origin = os.environ["LAND_ORIGIN"]
    with tempfile.TemporaryDirectory() as td:
        run = lambda *a, **kw: subprocess.run(["git", *a], cwd=td, check=True,
                                              capture_output=True, **kw)
        run("clone", "-q", origin, ".")
        env = dict(os.environ, GIT_COMMITTER_NAME="GitHub",
                   GIT_COMMITTER_EMAIL="rebase@example.com",
                   GIT_COMMITTER_DATE="2030-01-01T00:00:00Z")
        run("cherry-pick", "main..origin/" + st["branch"], env=env)
        run("push", "-q", "origin", "main", ":refs/heads/" + st["branch"])
    st["state"] = "MERGED"; save()
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:1] == ["api"]:
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if path.endswith("/rules/branches/main"):
        print(1 if st.get("rules") else 0); sys.exit(0)
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    st["branch"] = args[args.index("--head") + 1]; st["state"] = "OPEN"; save()
    print("https://github.com/taxjson/taxjson/pull/7"); sys.exit(0)
if args[:2] == ["pr", "merge"]:
    if "--disable-auto" in args:
        st["auto"] = False; save(); sys.exit(0)
    if "--auto" in args:
        if not st.get("auto_ok"):
            sys.exit(1)
        st["auto"] = True; save(); sys.exit(0)
    merge(); sys.exit(0)
if args[:2] == ["pr", "view"]:
    if st.get("auto") and st["checks"] == "pass" and st["state"] == "OPEN":
        merge()
    print(st["state"]); sys.exit(0)
if args[:2] == ["pr", "checks"]:
    if st["checks"] != "none":
        for name in ("ruff (critical tier)", "pii / new commits"):
            print("%s\t%s\t1m\thttps://x" % (name, st["checks"]))
    sys.exit(0 if st["checks"] == "pass" else 8)
if args[:2] == ["pr", "close"]:
    st["state"] = "CLOSED"; save(); sys.exit(0)
sys.exit(0)
"""


class TestLand(_Sandbox):
    def setUp(self):
        super().setUp()
        self.env.pop("TAXJSON_LAND_DIRECT", None)
        self.env["TAXJSON_LAND_POLL_SECS"] = "0"
        self.origin = self.d / "origin.git"
        self.git(self.d, "init", "-q", "--bare", "-b", "main",
                 str(self.origin))
        self.dev = self.d / "dev"
        self.git(self.d, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.dev / "scripts" / "hooks").mkdir(parents=True)
        for f in ("land.sh", "check-pii.sh", "hooks/pre-push"):
            shutil.copy(REPO / "scripts" / f, self.dev / "scripts" / f)
        (self.dev / "f.txt").write_text("0\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", "start")
        self.git(self.dev, "push", "-q", "origin", "main")
        self._stub("gh", _LAND_GH)
        self.state = self.d / "land.json"
        self.env["LAND_STATE"] = str(self.state)
        self.env["LAND_ORIGIN"] = str(self.origin)

    def set_state(self, **kw):
        st = {"checks": "pass", "rules": False, "auto_ok": False,
              "auto": False, "state": "NONE", "branch": ""}
        st.update(kw)
        self.state.write_text(json.dumps(st))

    def commit(self, msg="a change"):
        (self.dev / "f.txt").write_text(msg + "\n")
        self.git(self.dev, "commit", "-q", "-am", msg)
        return self.git(self.dev, "rev-parse", "HEAD")

    def land(self, *args, **env):
        return subprocess.run(["bash", str(self.dev / "scripts" / "land.sh"),
                               *args], cwd=self.d, capture_output=True,
                              text=True, env=dict(self.env, **env))

    def origin_main(self):
        return self.git(self.dev, "ls-remote", "origin",
                        "refs/heads/main").split()[0]

    def origin_branches(self):
        return [ln.split()[1] for ln in
                self.git(self.dev, "ls-remote", "--heads", "origin")
                .splitlines()]

    def calls(self, *prefix):
        return [c for c in self.gh_calls() if c[:len(prefix)] == list(prefix)]

    def test_without_required_checks_it_waits_and_merges_itself(self):
        self.set_state()
        mine = self.commit()
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("merging it (rebase) here once every check has passed",
                      r.stdout)
        self.assertFalse([c for c in self.calls("pr", "merge")
                          if "--auto" in c])
        landed = self.origin_main()
        self.assertNotEqual(landed, mine, "a rebase merge: a new id")
        self.assertEqual(self.git(self.dev, "rev-parse", "HEAD"), landed)
        self.assertEqual(self.git(self.dev, "log", "-1", "--format=%s"),
                         "a change")
        self.assertEqual(self.origin_branches(), ["refs/heads/main"])
        create = self.calls("pr", "create")[0]
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertEqual(create[create.index("--title") + 1], "a change")
        self.assertIn(f"✓ landed 1 commit(s) on main as {landed[:9]}",
                      r.stdout)

    def test_required_checks_use_auto_merge(self):
        self.set_state(rules=True, auto_ok=True)
        self.commit("one")
        self.commit("two")
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("GitHub merges it (rebase) once its required checks "
                      "pass", r.stdout)
        auto = [c for c in self.calls("pr", "merge") if "--auto" in c]
        self.assertEqual(len(auto), 1)
        self.assertIn("--rebase", auto[0])
        create = self.calls("pr", "create")[0]
        self.assertEqual(create[create.index("--title") + 1], "one (+1 more)")
        self.assertEqual(self.git(self.dev, "rev-parse", "HEAD"),
                         self.origin_main())

    def test_a_failed_check_closes_the_pull_request(self):
        self.set_state(rules=True, auto_ok=True, checks="fail")
        mine = self.commit()
        start = self.origin_main()
        r = self.land()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Check(s) failed: ruff (critical tier), pii / new "
                      "commits", r.stderr)
        self.assertIn("the pull request is closed", r.stderr)
        self.assertTrue([c for c in self.calls("pr", "merge")
                         if "--disable-auto" in c])
        close = self.calls("pr", "close")
        self.assertEqual(len(close), 1)
        self.assertIn("--delete-branch", close[0])
        self.assertEqual(self.origin_main(), start)
        self.assertEqual(self.git(self.dev, "rev-parse", "HEAD"), mine)

    def test_timeout_leaves_it_open(self):
        self.set_state(checks="pending")
        self.commit()
        r = self.land("--timeout", "0")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("merge it (gh pr merge https://github.com/taxjson/"
                      "taxjson/pull/7 --rebase --delete-branch)", r.stderr)
        self.assertFalse(self.calls("pr", "close"))

    def test_refusals_push_nothing(self):
        self.set_state()
        r = self.land()
        self.assertEqual(r.returncode, 0)
        self.assertIn("Nothing to land", r.stdout)
        # The PII gate refuses: nothing pushed, no pull request.
        hook = self.dev / "scripts" / "hooks" / "pre-push"
        hook.write_text("echo 'stub gate ran' >&2\nexit 1\n")
        hook.chmod(0o644)
        self.commit()
        r = self.land()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("stub gate ran", r.stderr)
        self.assertIn("pre-push gate refused — nothing pushed", r.stderr)
        self.assertEqual(self.origin_branches(), ["refs/heads/main"])
        self.assertFalse(self.calls("pr"))
        # Behind origin/main.
        other = self.d / "other"
        self.git(self.d, "clone", "-q", str(self.origin), str(other))
        (other / "g.txt").write_text("x\n")
        self.git(other, "add", "g.txt")
        self.git(other, "commit", "-q", "-m", "elsewhere")
        self.git(other, "push", "-q", "origin", "main")
        r = self.land()
        self.assertEqual(r.returncode, 2)
        self.assertIn("main moved on by 1 commit(s)", r.stderr)
        # Off main.
        self.git(self.dev, "checkout", "-q", "-b", "side")
        r = self.land()
        self.assertEqual(r.returncode, 2)
        self.assertIn("Land from main", r.stderr)

    def test_direct_escape_hatch(self):
        self.set_state()
        mine = self.commit()
        r = self.land(TAXJSON_LAND_DIRECT="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("direct push, TAXJSON_LAND_DIRECT=1", r.stdout)
        self.assertEqual(self.origin_main(), mine)
        self.assertFalse(self.calls("pr"))

    def test_never_no_verify(self):
        for f in ("land.sh", "release.sh", "promote.sh"):
            text = (REPO / "scripts" / f).read_text()
            code = "\n".join(ln for ln in text.splitlines()
                             if not ln.lstrip().startswith("#"))
            self.assertNotIn("--no-verify", code, f)


if __name__ == "__main__":
    unittest.main()

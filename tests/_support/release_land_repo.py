"""Shared test helpers: land.sh's stub gh and a fixture base: a dev clone of a bare origin with land.sh, its hooks and that stub.

Shared from: test_fix_release_hardening
"""
import json
import shutil
import subprocess

from _support.release_repo import _Sandbox
import _paths


REPO = _paths.REPO


# gh stand-in for land.sh's pull-request path. State in $LAND_STATE (JSON):
# checks ("pass" | "fail" | "pending" | "none"), rules (required checks on
# main?), auto_ok (may auto-merge be turned on?), auto, state, branch, runs
# (this branch's Actions run id), stale (a failed run of another branch on
# the same commit, as after an earlier landing: what a runs query that is
# not limited to this branch would see). The Actions API answers as through
# land.sh's --jq: runs as "id<TAB>status<TAB>name", jobs as "name<TAB>state".
# A merge rebases the branch's commits onto origin's main with GitHub as the
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
        # "Require branches to be up to date": no merge onto a main the
        # branch does not hold (TJ-0214).
        if st.get("strict") and subprocess.run(
                ["git", "merge-base", "--is-ancestor", "main",
                 "origin/" + st["branch"]], cwd=td).returncode:
            return False
        env = dict(os.environ, GIT_COMMITTER_NAME="GitHub",
                   GIT_COMMITTER_EMAIL="rebase@example.com",
                   GIT_COMMITTER_DATE="2030-01-01T00:00:00Z")
        run("cherry-pick", "main..origin/" + st["branch"], env=env)
        run("push", "-q", "origin", "main", ":refs/heads/" + st["branch"])
    st["state"] = "MERGED"; save()
    return True
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:1] == ["api"]:
    path = [a for a in args[1:] if not a.startswith("-")][0]
    if path.endswith("/rules/branches/main"):
        jq = args[args.index("--jq") + 1] if "--jq" in args else ""
        if "strict_required_status_checks_policy" in jq:
            print(1 if st.get("rules") and st.get("strict") else 0); sys.exit(0)
        print(1 if st.get("rules") else 0); sys.exit(0)
    if "/actions/runs?" in path:
        if "branch=%s&" % st["branch"] not in path:
            if st.get("stale"):
                print("1\tcompleted\tCI")
        elif st["checks"] != "none":
            print("%s\t%s\tCI" % (st.get("runs") or "9",
                                   "in_progress" if st["checks"] == "pending" else "completed"))
        sys.exit(0)
    if "/actions/runs/1/jobs" in path:
        print("ruff (critical tier)\tfail"); sys.exit(0)
    if "/jobs" in path:
        for name in ("ruff (critical tier)", "pii / new commits"):
            print("%s\t%s" % (name, st["checks"]))
        sys.exit(0)
    if path.endswith("/status"):
        sys.exit(0)
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
    sys.exit(0 if merge() else 1)
if args[:2] == ["pr", "view"]:
    if st.get("auto") and st["checks"] == "pass" and st["state"] == "OPEN":
        merge()
    print(st["state"]); sys.exit(0)
if args[:2] == ["run", "list"]:
    print(st.get("runs", "")); sys.exit(0)
if args[:2] == ["pr", "close"]:
    st["state"] = "CLOSED"; save(); sys.exit(0)
sys.exit(0)
"""


class _LandRepo(_Sandbox):
    """A dev clone of a bare origin with scripts/land.sh, its hooks and
    the stub gh (_LAND_GH) answering from a state file: the fixture of
    TestLand, and of the land.sh tests elsewhere."""

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

    def workflow(self, name, pull_request=True, paths=False):
        wf = self.dev / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        on = "  pull_request:\n" if pull_request else "  push:\n"
        if paths:
            on += "    paths:\n      - scripts/x.sh\n"
        (wf / f"{name.lower()}.yml").write_text(
            f"name: {name}\non:\n{on}permissions:\n  contents: read\n")

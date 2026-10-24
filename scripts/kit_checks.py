#!/usr/bin/env python3
"""The kit's dependency and code checks (SPEC.md section 14; PLAN.md D24, D25), run by a dev repo's
CI and by `kit preflight`. A dev repo keeps an unchanged copy as scripts/kit_checks.py, which
`kit doctor` compares with the kit's checks/kit_checks.py.

  python3 -I scripts/kit_checks.py [--repo DIR] [--only deps|code] [--cache DIR]
                                   [--osv-scanner PATH] [--opengrep PATH]

deps: osv-scanner reads the lock files kit-checks.toml lists and asks OSV about each pinned version.
code: opengrep runs the kit's selection of the opengrep-rules security rules over the tree.
Both tools and the rules are pinned here and checked (sha256; the rules by their git commit), and
fetched once into the cache. Every hit blocks unless kit-checks.toml excepts it: one advisory in one
lock file, or one rule in one path, each with a reason and an expiry date; an expired exception
fails like a hit. Inline `nosemgrep` comments, .semgrepignore files and osv-scanner.toml are not
honoured: the exceptions are in kit-checks.toml, in one place. Prints advisory ids, rule ids and
locations, never a line of the code. Exit 0 clean, 1 a hit, 3 incomplete (as the gate).

Python 3.11 or later, standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

CLEAN, HIT, INCOMPLETE = 0, 1, 3
CONFIG = "kit-checks.toml"
SCRIPT_PATH = "scripts/kit_checks.py"          # the dev repo's copy, which doctor compares
WARN_DAYS = 14                                 # doctor lists exceptions expiring this soon

OSV_VERSION = "2.6.0"
OSV_URL = "https://github.com/google/osv-scanner/releases/download/v{version}/{asset}"
OSV_BUILDS = {   # platform.machine() -> (release asset, sha256)
    "x86_64": ("osv-scanner_linux_amd64", "ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108"),
    "aarch64": ("osv-scanner_linux_arm64", "2c71403eb443d05891c4f268c3ad771cf4f16e5443463fd7851ef8f454d3c7e4"),
}
OPENGREP_VERSION = "1.30.2"
OPENGREP_URL = "https://github.com/opengrep/opengrep/releases/download/v{version}/{asset}"
OPENGREP_BUILDS = {
    "x86_64": ("opengrep_manylinux_x86", "a66aa3278457f02b287b985a45b6762aebcaba5000f2689245fd1ed86d1456c7"),
    "aarch64": ("opengrep_manylinux_aarch64", "90acea5df4b733083f388d4feeb250ca802f671b33d1f222653d1e571dd2b0d8"),
}
# The rules: opengrep's fork of semgrep-rules from before their licence changed (LGPL-2.1 with the
# Commons Clause). Fetched by commit id, so git checks every object; never copied into a repo.
RULES_REPO = "https://github.com/opengrep/opengrep-rules"
RULES_COMMIT = "f1d2b562b414783763fd02a6ed2736eaed622efa"
RULE_DIRS = ("python", "javascript", "typescript", "yaml/github-actions", "dockerfile", "bash", "generic")
RULES_MARK = "kit-rules"                       # the selection's folder name, stripped from rule ids
IGNORE_FILES = (".semgrepignore", ".opengrepignore")
GLOB = re.compile(r"[*?\[\]]")


class Incomplete(Exception):
    """The checks could not run as asked; the message says what to do."""


# --- kit-checks.toml ----------------------------------------------------------------

def load_config(text: str) -> dict:
    """kit-checks.toml, checked: {"lock_files": [(osv's -L argument, path)], "vuln_exceptions",
    "code_exceptions"}. Anything it does not know is refused, so a misspelt key never passes as
    "no exceptions" or "no lock files"."""
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Incomplete(f"{CONFIG} is not valid TOML ({e})") from None
    unknown = sorted(set(cfg) - {"lock_files", "vuln_exceptions", "code_exceptions"})
    if unknown:
        raise Incomplete(f"{CONFIG}: unknown keys {', '.join(unknown)} (lock_files, vuln_exceptions, code_exceptions)")
    files = cfg.get("lock_files")
    if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files):
        raise Incomplete(f'{CONFIG}: lock_files must be a list of paths (lock_files = [] when there are none)')
    locks = []
    for f in files:
        parser, sep, path = f.rpartition(":")
        if not sep:
            parser, path = ("requirements.txt" if f.endswith(".txt") else ""), f
        literal_path(path, "lock_files")
        locks.append((f"{parser}:{path}" if parser else path, path))
    paths = {p for _, p in locks}
    out = {"lock_files": locks, "vuln_exceptions": [], "code_exceptions": []}
    for kind, keys in (("vuln_exceptions", ("id", "lock_file")), ("code_exceptions", ("rule", "path"))):
        entries = cfg.get(kind, [])
        if not isinstance(entries, list):
            raise Incomplete(f"{CONFIG}: {kind} must be [[{kind}]] tables")
        for i, e in enumerate(entries, 1):
            where = f"{CONFIG}: {kind} entry {i}"
            if not isinstance(e, dict) or set(e) != {*keys, "reason", "expires"}:
                raise Incomplete(f"{where} must have exactly {', '.join(keys)}, reason and expires")
            for k in (*keys, "reason"):
                if not isinstance(e[k], str) or not e[k].strip():
                    raise Incomplete(f"{where}: {k} must be a non-empty string")
            if not isinstance(e["expires"], dt.date) or isinstance(e["expires"], dt.datetime):
                raise Incomplete(f"{where}: expires must be a date, written expires = 2026-12-31 (no quotes)")
            for k in keys:
                literal_path(e[k], f"{kind} {k}")
            if kind == "vuln_exceptions" and e["lock_file"] not in paths:
                raise Incomplete(f"{where}: lock_file {e['lock_file']} is not in lock_files")
            out[kind].append(e)
    return out


def literal_path(value: str, what: str) -> None:
    """One exact name: no globs, nothing absolute, no way out of the repo."""
    if GLOB.search(value) or value.startswith("/") or ".." in value.split("/"):
        raise Incomplete(f"{CONFIG}: {what} {value!r} must be one exact repo path or id (no globs, no '..', not absolute)")


def expiring(cfg: dict, today: dt.date) -> tuple[list[str], list[str]]:
    """(expired exceptions, those expiring within WARN_DAYS), each as one line."""
    expired, soon = [], []
    for kind, keys in (("vuln_exceptions", ("id", "lock_file")), ("code_exceptions", ("rule", "path"))):
        for e in cfg[kind]:
            line = f"{e[keys[0]]} in {e[keys[1]]} (expires {e['expires']})"
            if e["expires"] < today:
                expired.append(line)
            elif e["expires"] <= today + dt.timedelta(days=WARN_DAYS):
                soon.append(line)
    return expired, soon


# --- the tools ----------------------------------------------------------------------

def cache_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    if os.environ.get("KIT_CHECKS_CACHE"):
        return Path(os.environ["KIT_CHECKS_CACHE"]).expanduser().resolve()
    if os.environ.get("RUNNER_TOOL_CACHE"):                         # an Actions runner
        return Path(os.environ["RUNNER_TOOL_CACHE"]) / "kit-checks"
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "publish-kit" / "checks"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, sha256: str, dest: Path) -> None:
    """Download url to dest, executable, only when its sha256 is the pinned one."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    try:
        h = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=300) as r:  # noqa: S310 (pinned URL, checked)
            for chunk in iter(lambda: r.read(1 << 20), b""):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != sha256:
            raise Incomplete(f"{url} is not the pinned build (sha256 {h.hexdigest()}, expected {sha256}); nothing kept")
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    except OSError as e:
        raise Incomplete(f"could not download {url} ({e})") from None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def tool(name: str, given: str | None, env: str, version: str, url: str, builds: dict, cache: Path) -> tuple[str, str]:
    """(the binary, how it was found). A given one (--<name> or $<env>) is used as it is; else the
    pinned build, from the cache when its sha256 is still right, else downloaded."""
    given = given or os.environ.get(env)
    if given:
        p = shutil.which(given) if os.sep not in given else given
        if not p or not os.access(p, os.X_OK):
            raise Incomplete(f"{name} {given}: not an executable file")
        return os.path.abspath(p), "given"
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if platform.system() != "Linux" or machine not in builds:
        raise Incomplete(f"no pinned {name} build for {platform.system()} {machine}: give --{name} PATH")
    asset, sha = builds[machine]
    dest = cache / f"{name}-{version}-{machine}"
    if dest.is_file() and file_sha256(dest) == sha:
        return str(dest), f"{version}, pinned"
    fetch(url.format(version=version, asset=asset), sha, dest)
    return str(dest), f"{version}, pinned, downloaded"


def git(d: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
    return r.returncode, (r.stdout + r.stderr).strip()


def rules_checkout(cache: Path) -> Path:
    """opengrep-rules at RULES_COMMIT, unchanged (a cached one is checked again each time)."""
    d = cache / f"opengrep-rules-{RULES_COMMIT[:12]}"
    if d.is_dir() and git(d, "rev-parse", "HEAD") == (0, RULES_COMMIT) \
            and git(d, "status", "--porcelain", "--untracked-files=all") == (0, ""):
        return d
    shutil.rmtree(d, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(dir=cache, prefix=".rules."))
    try:
        for args in (("init", "-q"), ("fetch", "-q", "--depth", "1", RULES_REPO, RULES_COMMIT),
                     ("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD")):
            code, out = git(tmp, *args)
            if code != 0:
                raise Incomplete(f"could not fetch {RULES_REPO} at {RULES_COMMIT[:12]} (git {args[0]}: {out[-200:]})")
        if git(tmp, "rev-parse", "HEAD") != (0, RULES_COMMIT):
            raise Incomplete(f"{RULES_REPO} did not give commit {RULES_COMMIT}")
        os.replace(tmp, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return d


def selected(rel: str, text: str) -> bool:
    """The kit's selection: security rules (not audit ones, not the secrets ones gitleaks covers),
    leaving out a file whose every rule is of low confidence."""
    parts = rel.split("/")
    if not rel.endswith(".yaml") or rel.endswith(".test.yaml") or ".fixed." in parts[-1]:
        return False
    if not any(rel.startswith(d + "/") for d in RULE_DIRS) or rel.startswith("generic/secrets/"):
        return False
    if "security" not in parts[:-1] or "audit" in parts[:-1]:
        return False
    ids = len(re.findall(r"^\s*-\s+id:", text, re.M))
    low = len(re.findall(r"^\s*confidence:\s*LOW\b", text, re.M))
    return ids > 0 and low < ids


def select_rules(rules: Path, dest: Path) -> int:
    """Copy the selection into dest; how many rule files."""
    n = 0
    for f in sorted(rules.rglob("*.yaml")):
        rel = f.relative_to(rules).as_posix()
        text = f.read_text(errors="replace")
        if selected(rel, text):
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text(text)
            n += 1
    return n


# --- the checks ---------------------------------------------------------------------

def deps(repo: Path, cfg: dict, osv: str, today: dt.date) -> tuple[list[dict], str]:
    """osv-scanner over the lock files: (hits as {"ids", "aliases", "package", "version", "lock_file",
    "severity"}, a summary)."""
    if not cfg["lock_files"]:
        return [], "no lock_files listed"
    missing = [p for _, p in cfg["lock_files"] if not (repo / p).is_file()]
    if missing:
        raise Incomplete(f"lock_files not in the tree: {', '.join(missing)} (fix {CONFIG})")
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "osv-scanner.toml"            # instead of any osv-scanner.toml in the repo
        empty.write_text("")
        args = [osv, "scan", "source", "--format", "json", "--config", str(empty)]
        for spec, _ in cfg["lock_files"]:
            args += ["-L", spec]
        r = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise Incomplete(f"osv-scanner exited {r.returncode}: {r.stderr.strip()[-300:]}")
    try:
        report = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        raise Incomplete("osv-scanner's output is not JSON") from None
    hits = []
    for res in report.get("results") or []:
        src = Path(res.get("source", {}).get("path", ""))
        lock = os.path.relpath(src, repo) if src.is_absolute() else src.as_posix()
        for pkg in res.get("packages") or []:
            p = pkg.get("package", {})
            for g in pkg.get("groups") or []:
                hits.append({"ids": list(g.get("ids") or []), "aliases": list(g.get("aliases") or []),
                             "package": p.get("name", "?"), "version": p.get("version", "?"),
                             "lock_file": lock, "severity": g.get("max_severity") or ""})
    if r.returncode == 1 and not hits:
        raise Incomplete("osv-scanner found vulnerabilities but its report lists none")
    return hits, f"{len(cfg['lock_files'])} lock files"


def code(repo: Path, opengrep: str, rules: Path) -> tuple[list[dict], str]:
    """opengrep with the selected rules over the tree: (hits as {"rule", "path", "line"}, a summary)."""
    ignores = sorted(p.relative_to(repo).as_posix() for name in IGNORE_FILES for p in repo.rglob(name)
                     if not {".git", "node_modules"} & set(p.relative_to(repo).parts))
    if ignores:
        raise Incomplete(f"{', '.join(ignores)}: the kit's code check honours no ignore files; remove them, "
                         f"and except a finding in {CONFIG} instead (one rule, one path, a reason, an expiry)")
    with tempfile.TemporaryDirectory() as tmp:
        sel = Path(tmp) / RULES_MARK
        n = select_rules(rules, sel)
        if n == 0:
            raise Incomplete(f"no rules selected from {rules}")
        # --no-git-ignore: the same files in CI (a checkout) and in preflight (an exported tree, no .git):
        # every file there, less opengrep's own defaults (tests, node_modules, vendored and minified code).
        r = subprocess.run([opengrep, "scan", "--config", str(sel), "--json", "--disable-nosem", "--no-git-ignore",
                            "--quiet", "."], cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise Incomplete(f"opengrep exited {r.returncode}: {r.stderr.strip()[-300:]}")
    try:
        report = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise Incomplete("opengrep's output is not JSON") from None
    errors = [e for e in report.get("errors") or [] if e.get("level") != "warn"]
    if errors:   # a rule that did not load, a file it stopped on: not a clean answer
        kinds = sorted({str(e.get("type") if isinstance(e.get("type"), str) else (e.get("type") or ["?"])[0]) for e in errors})
        raise Incomplete(f"opengrep reported {len(errors)} errors ({', '.join(kinds)})")
    partial = len({e.get("path") for e in report.get("errors") or [] if e.get("level") == "warn"})
    hits = [{"rule": h.get("check_id", "?").split(RULES_MARK + ".", 1)[-1], "path": h.get("path", "?"),
             "line": h.get("start", {}).get("line", 0)} for h in report.get("results") or []]
    scanned = len((report.get("paths") or {}).get("scanned") or [])
    return hits, (f"rules {RULES_COMMIT[:12]} ({n} files), {scanned} files scanned"
                  + (f" ({partial} parsed in part)" if partial else ""))


def apply_exceptions(hits: list[dict], entries: list[dict], match, today: dt.date) -> tuple[list, list, set]:
    """(blocking hits, excepted hits, the ids of the exceptions used). An expired exception excepts nothing."""
    blocking, excepted, used = [], [], set()
    for h in hits:
        e = next((i for i, e in enumerate(entries) if match(h, e) and e["expires"] >= today), None)
        if e is None:
            blocking.append(h)
        else:
            excepted.append(h)
            used.add(e)
    return blocking, excepted, used


def vuln_match(h: dict, e: dict) -> bool:
    return e["lock_file"] == h["lock_file"] and e["id"] in set(h["ids"]) | set(h["aliases"])


def code_match(h: dict, e: dict) -> bool:
    return e["rule"] == h["rule"] and e["path"] == h["path"]


def hits_text(blocking: list, excepted: list) -> str:
    return f"{len(blocking)} hit{'' if len(blocking) == 1 else 's'}, {len(excepted)} excepted"


def check(repo: Path, only: str | None = None, cache: str | None = None, osv: str | None = None,
          opengrep: str | None = None, today: dt.date | None = None, out=print, where: str | None = None) -> int:
    """Run the checks on the tree at repo, printing what it finds; the exit code. `where` names the
    tree in messages (kit preflight's is a temporary copy of a commit)."""
    today = today or dt.date.today()
    try:
        cfg_file = repo / CONFIG
        if not cfg_file.is_file():
            raise Incomplete(f"no {CONFIG} at the top of {where or repo}: add one (lock_files = [...], "
                             "the kit's SPEC.md section 14; templates/ci/ has a starter)")
        cfg = load_config(cfg_file.read_text())
    except Incomplete as e:
        out(f"kit checks: incomplete: {e}")
        return INCOMPLETE
    cdir = cache_dir(cache)
    incomplete, blocking_total = False, 0
    for part in ("deps", "code"):
        if only and only != part:
            continue
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            if part == "deps":
                binary, how = tool("osv-scanner", osv, "KIT_OSV_SCANNER", OSV_VERSION, OSV_URL, OSV_BUILDS, cdir)
                hits, summary = deps(repo, cfg, binary, today)
                blocking, excepted, used = apply_exceptions(hits, cfg["vuln_exceptions"], vuln_match, today)
                out(f"  deps: osv-scanner ({how}), {summary}: {hits_text(blocking, excepted)}")
                for h in blocking:
                    ids = ", ".join(h["ids"]) or "?"
                    out(f"    [deps] {ids} {h['package']} {h['version']} in {h['lock_file']}"
                        + (f" (severity {h['severity']})" if h["severity"] else ""))
                entries, key = cfg["vuln_exceptions"], ("id", "lock_file")
            else:
                binary, how = tool("opengrep", opengrep, "KIT_OPENGREP", OPENGREP_VERSION, OPENGREP_URL,
                                   OPENGREP_BUILDS, cdir)
                rules = Path(os.environ["KIT_CHECKS_RULES"]) if os.environ.get("KIT_CHECKS_RULES") else rules_checkout(cdir)
                hits, summary = code(repo, binary, rules)
                blocking, excepted, used = apply_exceptions(hits, cfg["code_exceptions"], code_match, today)
                out(f"  code: opengrep ({how}), {summary}: {hits_text(blocking, excepted)}")
                for h in blocking:
                    out(f"    [code] {h['rule']} {h['path']}:{h['line']}")
                entries, key = cfg["code_exceptions"], ("rule", "path")
        except (Incomplete, OSError) as e:
            out(f"  {part}: incomplete: {e}")
            incomplete = True
            continue
        for i, e in enumerate(entries):
            if e["expires"] < today:
                out(f"    [{part}] exception for {e[key[0]]} in {e[key[1]]} expired on {e['expires']}: "
                    "it now fails; fix the finding, or renew the exception with a new date and reason")
                blocking_total += 1
            elif i not in used:
                out(f"    note: the exception for {e[key[0]]} in {e[key[1]]} matched nothing: remove it")
        blocking_total += len(blocking)
    if blocking_total:
        out(f"kit checks: blocked ({blocking_total}). Fix them, or except one in {CONFIG}: one advisory id and "
            "its lock file, or one rule and its path, with a reason and an expiry date.")
        return HIT
    if incomplete:
        out("kit checks: incomplete (above); nothing was found in what did run.")
        return INCOMPLETE
    out("kit checks: clean.")
    return CLEAN


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="the tree to check (default: the current folder)")
    ap.add_argument("--only", choices=["deps", "code"])
    ap.add_argument("--cache", help="where the pinned tools and rules are kept")
    ap.add_argument("--osv-scanner", help="an osv-scanner binary (default: the pinned build)")
    ap.add_argument("--opengrep", help="an opengrep binary (default: the pinned build)")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    print(f"kit checks: {repo}")
    return check(repo, a.only, a.cache, a.osv_scanner, a.opengrep)


if __name__ == "__main__":
    sys.exit(main())

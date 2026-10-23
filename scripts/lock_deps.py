#!/usr/bin/env python3
"""lock_deps — the hash-pinned dependency locks install.sh installs from.

install.sh never resolves a dependency by name from PyPI: it installs
requirements/*.txt with `pip --require-hashes --only-binary :all:`, then
taxjson itself (and taxjson-fetch) with --no-deps from the release
checkout. A release someone uploads to PyPI later (a hijacked account, a
typo-squat pulled in by a new dependency) cannot reach a new install: its
files have hashes no lock names.

The locks (requirements/, one file each):
  build.txt   pip and setuptools: the installer's pip, and the build
              backend taxjson and taxjson-fetch are installed with
              (--no-build-isolation, so pip never fetches setuptools
              unpinned into a build environment)
  core.txt    taxjson's own dependencies, plus taxjson-fetch's (besides
              taxjson itself)
  <extra>.txt core.txt plus one [project.optional-dependencies] extra
              (fx, the installer's default; xlsx; ...). install.sh
              installs "fx,xlsx" as -r fx.txt -r xlsx.txt: every lock is
              resolved under one shared resolution, so a package two
              locks name has the same version and hashes in both.

Each lock is a universal resolution (`uv pip compile --universal
--generate-hashes`, Python >= 3.10): one file for every platform and
Python version, with environment markers where versions differ (numpy
for Python 3.10 is not numpy for 3.13). Its hashes are every file PyPI
has for each pinned version, so the same lock serves Linux x86_64 and
aarch64, macOS arm64 and x86_64, and Python 3.10-3.13 (`--coverage`
checks a wheel exists for each). Nothing newer than the cool-down
(default 7 days) is locked: a hijacked release is usually yanked within
days.

  scripts/lock_deps.py              regenerate every lock (needs uv and
                                    the network), then run --coverage
  scripts/lock_deps.py --check      offline: every lock matches
                                    pyproject.toml and was not edited by
                                    hand (tests/test_dependency_locks.py)
  scripts/lock_deps.py --coverage   network: a wheel exists for every
                                    target platform and Python version

Options: --cooldown-days N (default 7; 0 = up to now), --uv PATH,
--security-update PKG (repeatable: PKG alone skips the cooldown, for a
fix to a known vulnerability published within it; named in the header).
When to run it: docs/releasing.md ("Dependency locks").
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ImportError:                       # Python < 3.11
    import tomli as tomllib               # a core dependency there

REPO = Path(__file__).resolve().parent.parent
LOCK_DIR = REPO / "requirements"
CORE_PYPROJECT = REPO / "pyproject.toml"
FETCH_PYPROJECT = REPO / "packages" / "taxjson-fetch" / "pyproject.toml"
PYTHON_FLOOR = "3.10"

# Where users run the installer (README: Linux and macOS), and the Python
# versions the package supports. A lock in REQUIRED must have a wheel for
# every pair (or regeneration fails); the other extras are reported only
# (install.sh falls back to the core package when a wheel is missing).
PLATFORMS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
)
PYTHONS = ("3.10", "3.11", "3.12", "3.13")
REQUIRED = ("build", "core", "fx")

HEADER_MARK = "# taxjson dependency lock"
_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def req_name(req):
    m = _NAME.match(req)
    if not m:
        raise SystemExit(f"lock_deps: cannot read the requirement {req!r}")
    return norm(m.group(1))


def _load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def lock_inputs():
    """{lock name: sorted requirement strings} from the pyproject files."""
    core = _load(CORE_PYPROJECT)
    fetch = _load(FETCH_PYPROJECT)
    build = set(core.get("build-system", {}).get("requires", []))
    build |= set(fetch.get("build-system", {}).get("requires", []))
    build.add("pip")
    base = set(core["project"].get("dependencies", []))
    # taxjson-fetch is installed --no-deps beside the core: whatever it
    # needs besides taxjson must come from the core lock.
    base |= {r for r in fetch["project"].get("dependencies", [])
             if req_name(r) != "taxjson"}
    out = {"build": sorted(build), "core": sorted(base)}
    for extra, reqs in core["project"].get("optional-dependencies", {}).items():
        if extra in out:
            raise SystemExit(f"lock_deps: an extra named {extra!r} clashes "
                             f"with the {extra}.txt lock")
        for r in reqs:
            if req_name(r) == "taxjson":
                raise SystemExit(f"lock_deps: extra [{extra}] names taxjson "
                                 "itself; list its packages instead")
        out[extra] = sorted(base | set(reqs))
    return out


def inputs_digest(name, reqs):
    blob = json.dumps({"lock": name, "python": ">=" + PYTHON_FLOOR,
                       "requires": reqs}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def body_digest(body):
    return hashlib.sha256(body.encode()).hexdigest()


def split_lock(text):
    """(header fields, body) of a lock file."""
    lines = text.splitlines(keepends=True)
    fields, i = {}, 0
    while i < len(lines) and lines[i].startswith("#"):
        m = re.match(r"#\s*([a-z0-9-]+):\s*(.*)$", lines[i].rstrip("\n"))
        if m:
            fields[m.group(1)] = m.group(2).strip()
        i += 1
    if i < len(lines) and not lines[i].strip():
        i += 1
    return fields, "".join(lines[i:])


def pins(body):
    """[(name, version, has_hash)] of a lock body."""
    out, cur = [], None
    for line in body.splitlines():
        m = _PIN.match(line)
        if m:
            cur = [norm(m.group(1)), m.group(2), False]
            out.append(cur)
        elif cur is not None and line.strip().startswith("--hash=sha256:"):
            cur[2] = True
        elif line and not line.startswith((" ", "#")):
            raise ValueError(f"not a pinned requirement: {line!r}")
    return [tuple(p) for p in out]


def check(lock_dir=LOCK_DIR):
    """Problems (strings) between the locks and pyproject.toml; [] = ok."""
    problems = []
    want = lock_inputs()
    fix = "run scripts/lock_deps.py (docs/releasing.md, Dependency locks)"
    have = {p.stem for p in Path(lock_dir).glob("*.txt")}
    for name in sorted(have - set(want)):
        problems.append(f"requirements/{name}.txt: no such extra in "
                        f"pyproject.toml any more — {fix}")
    for name, reqs in sorted(want.items()):
        path = Path(lock_dir) / f"{name}.txt"
        if not path.is_file():
            problems.append(f"requirements/{name}.txt is missing — {fix}")
            continue
        fields, body = split_lock(path.read_text(encoding="utf-8"))
        if fields.get("inputs-sha256") != inputs_digest(name, reqs):
            problems.append(f"requirements/{name}.txt was locked from other "
                            f"requirements than pyproject.toml's — {fix}")
        if fields.get("body-sha256") != body_digest(body):
            problems.append(f"requirements/{name}.txt was edited by hand "
                            f"(its body does not match body-sha256) — {fix}")
        try:
            pinned = pins(body)
        except ValueError as e:
            problems.append(f"requirements/{name}.txt: {e}")
            continue
        for pkg, ver, hashed in pinned:
            if not hashed:
                problems.append(f"requirements/{name}.txt: {pkg}=={ver} has "
                                f"no --hash — {fix}")
        names = {p[0] for p in pinned}
        for r in reqs:
            if req_name(r) not in names:
                problems.append(f"requirements/{name}.txt does not pin "
                                f"{req_name(r)} — {fix}")
    return problems


def find_uv(given):
    uv = given or os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise SystemExit("lock_deps: needs uv (https://docs.astral.sh/uv/; "
                         "e.g. `pipx install uv`)")
    # uv runs from a temporary folder: a relative path would not be found.
    return os.path.abspath(uv) if os.sep in uv else uv


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit(f"lock_deps: failed: {' '.join(cmd)}")
    return r


def compile_locks(uv, cooldown_days, security_updates=()):
    want = lock_inputs()
    cutoff = None
    now = _dt.datetime.now(_dt.timezone.utc)
    if cooldown_days > 0:
        t = now - _dt.timedelta(days=cooldown_days)
        cutoff = t.strftime("%Y-%m-%dT00:00:00Z")
    fresh = sorted({p.strip().lower() for p in security_updates if p.strip()})
    uv_version = run([uv, "--version"], REPO).stdout.split()[1]
    base = [uv, "pip", "compile", "--quiet", "--universal",
            "--python-version", PYTHON_FLOOR, "--no-header",
            "--index-url", "https://pypi.org/simple"]
    if cutoff:
        base += ["--exclude-newer", cutoff]
        for pkg in fresh:
            base += ["--exclude-newer-package",
                     f"{pkg}={now.strftime('%Y-%m-%dT%H:%M:%SZ')}"]
    LOCK_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # One resolution of everything, so every lock agrees on versions
        # (install.sh may combine several in one pip install).
        union = sorted(set().union(*want.values()))
        (tmp / "all-locks.in").write_text("\n".join(union) + "\n")
        run(base + ["all-locks.in", "-o", "all-locks.txt"], tmp)
        for name, reqs in sorted(want.items()):
            (tmp / f"{name}.in").write_text("\n".join(reqs) + "\n")
            run(base + ["--generate-hashes", "-c", "all-locks.txt",
                        f"{name}.in", "-o", f"{name}.txt"], tmp)
            body = (tmp / f"{name}.txt").read_text()
            what = {"build": "pip and the build backend",
                    "core": "taxjson's (and taxjson-fetch's) dependencies"
                    }.get(name, f"the core plus the [{name}] extra")
            header = [
                f"{HEADER_MARK}: {what}.",
                "# install.sh installs it with pip --require-hashes "
                "--only-binary :all:.",
                "# Generated by scripts/lock_deps.py; do not edit by hand.",
                f"# inputs-sha256: {inputs_digest(name, reqs)}",
                f"# body-sha256: {body_digest(body)}",
                f"# python: >={PYTHON_FLOOR}",
                f"# exclude-newer: {cutoff or 'none'}",
                f"# security-updates: {', '.join(fresh) if cutoff and fresh else 'none'}",
                f"# uv: {uv_version}",
            ]
            (LOCK_DIR / f"{name}.txt").write_text(
                "\n".join(header) + "\n\n" + body, encoding="utf-8")
    for stale in LOCK_DIR.glob("*.txt"):
        if stale.stem not in want:
            stale.unlink()
            print(f"removed requirements/{stale.name} (no such extra)")
    print(f"locked {', '.join(sorted(want))} into requirements/ "
          f"(uv {uv_version}, exclude-newer {cutoff or 'none'})")


def coverage(uv):
    """A wheel exists for every PLATFORMS x PYTHONS pair: 0 when every
    REQUIRED lock is covered."""
    failed_required = False
    names = sorted(lock_inputs(), key=lambda n: (n not in REQUIRED, n))
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            lock = LOCK_DIR / f"{name}.txt"
            gaps = []
            for plat in PLATFORMS:
                for py in PYTHONS:
                    r = subprocess.run(
                        [uv, "pip", "compile", "--quiet", "--no-deps",
                         "--only-binary", ":all:", "--no-header",
                         "--index-url", "https://pypi.org/simple",
                         "--python-platform", plat, "--python-version", py,
                         str(lock), "-o", str(Path(tmp) / "out.txt")],
                        capture_output=True, text=True)
                    if r.returncode != 0:
                        why = [ln.strip() for ln in r.stderr.splitlines()
                               if "has no" in ln or "no wheels" in ln]
                        gaps.append(f"{plat} / Python {py}: "
                                    f"{why[0] if why else r.stderr.strip()[-200:]}")
            tag = "required" if name in REQUIRED else "optional"
            if not gaps:
                print(f"ok    {name}.txt ({tag}): every platform and Python")
            else:
                print(f"GAPS  {name}.txt ({tag}): {len(gaps)} of "
                      f"{len(PLATFORMS) * len(PYTHONS)}")
                for g in gaps:
                    print(f"        {g}")
                failed_required |= name in REQUIRED
    return 1 if failed_required else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scripts/lock_deps.py",
        description="Regenerate or check requirements/*.txt, the "
                    "hash-pinned locks install.sh installs.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="offline: the locks match pyproject.toml")
    g.add_argument("--coverage", action="store_true",
                   help="network: wheels exist for every target")
    ap.add_argument("--cooldown-days", type=int, default=7)
    ap.add_argument("--uv", help="the uv binary (default: uv on PATH)")
    ap.add_argument("--security-update", action="append", default=[],
                    metavar="PKG", help="let PKG past the cooldown (a "
                    "fix to a known vulnerability); repeatable")
    a = ap.parse_args(argv)
    if a.check:
        problems = check()
        for p in problems:
            print(f"lock_deps: {p}", file=sys.stderr)
        if not problems:
            print("lock_deps: requirements/*.txt match pyproject.toml")
        return 1 if problems else 0
    uv = find_uv(a.uv)
    if a.coverage:
        return coverage(uv)
    compile_locks(uv, a.cooldown_days, a.security_update)
    problems = check()
    for p in problems:
        print(f"lock_deps: {p}", file=sys.stderr)
    if problems:
        return 1
    return coverage(uv)


if __name__ == "__main__":
    sys.exit(main())

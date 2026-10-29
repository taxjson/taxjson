#!/usr/bin/env python3
"""editable_check — the venv's editable installs match this checkout.

scripts/dev-setup.sh installs taxjson and the broker-fetch plugin
(taxjson-fetch) editable, and scripts/release.sh reinstalls them after
a version bump. An install's metadata (the dist-info in the venv: its
version, its entry points) is written when it is installed and never
again, so a version bump leaves it behind until the next install: the
dev venv kept taxjson-fetch 0.30.1 while the code was 0.37.0 (backlog
TJ-0636). setuptools also leaves a <name>.egg-info beside the sources
it built, which a process with that source folder on PYTHONPATH finds
before the venv's metadata: a stale version, and the plugin's entry
point in a run meant to have none (TJ-0594).

  venv/bin/python -I scripts/editable_check.py           versions match
  venv/bin/python -I scripts/editable_check.py --clean   first remove the
                                                         in-tree egg-info

Exit 1 when a version differs (re-run scripts/dev-setup.sh). taxjson must
be installed; a venv without taxjson-fetch is fine (CI's core jobs).
"""
import argparse
import shutil
import sys
from importlib import metadata
from pathlib import Path

try:
    import tomllib
except ImportError:                       # Python < 3.11
    import tomli as tomllib               # a core dependency there

REPO = Path(__file__).resolve().parent.parent

# (distribution, its pyproject.toml, required)
PACKAGES = (
    ("taxjson", Path("pyproject.toml"), True),
    ("taxjson-fetch", Path("packages/taxjson-fetch/pyproject.toml"), False),
)


def egg_infos(root=None):
    """The <name>.egg-info folders editable builds left in the source
    folders (src/, packages/*/src/)."""
    root = Path(root or REPO)
    return sorted([*(root / "src").glob("*.egg-info"),
                   *(root / "packages").glob("*/src/*.egg-info")])


def clean(root=None):
    """Remove them; the paths removed."""
    gone = []
    for p in egg_infos(root):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
            gone.append(p)
    return gone


def problems(root=None, version=None):
    """One line per installed distribution whose version is not the
    code's (or taxjson not installed)."""
    root, version = Path(root or REPO), version or metadata.version
    out = []
    for name, rel, required in PACKAGES:
        with open(root / rel, "rb") as f:
            want = tomllib.load(f)["project"]["version"]
        try:
            have = version(name)
        except metadata.PackageNotFoundError:
            if required:
                out.append(f"{name} is not installed in this venv: run "
                           f"scripts/dev-setup.sh")
            continue
        if have != want:
            out.append(f"the venv's {name} is {have}, the code {want} "
                       f"({rel}): re-run scripts/dev-setup.sh")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scripts/editable_check.py",
        description="The venv's taxjson and taxjson-fetch versions match "
                    "this checkout's pyproject.toml files.")
    ap.add_argument("--clean", action="store_true",
                    help="first remove the egg-info folders editable "
                         "builds leave in src/ and packages/*/src/")
    a = ap.parse_args(argv)
    if a.clean:
        for p in clean(REPO):
            print(f"   removed {p.relative_to(REPO)} (an editable build's "
                  f"leftover; the venv holds the metadata)")
    bad = problems(REPO)
    for p in bad:
        print(f"editable_check: {p}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

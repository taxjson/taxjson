#!/usr/bin/env python3
"""hide_optional — a sitecustomize.py that makes Python see a core
install: the optional extras, the broker-fetch plugin, or both, not
installed.

  scripts/hide_optional.py DIR [--extras "yfinance pandas ..."] [--plugin taxjson_fetch]

writes DIR/sitecustomize.py; put DIR first on PYTHONPATH. GitHub's
matrix jobs install taxjson without the extras and run the suite before
they install taxjson-fetch, while a developer's venv has both
(scripts/dev-setup.sh): scripts/ci_no_extras.sh hides both for the
tests that touch them, and scripts/ci.sh's suite stage hides the plugin
(backlog TJ-0267, TJ-0594), so a test that assumes either fails here as
it fails there.

  * an extra (an import name in --extras) installed in site-packages
    does not import; a test's own stand-in on PYTHONPATH (a fake
    yfinance package) still does;
  * the plugin (--plugin, its import name): no distribution of that
    name is found, wherever its metadata lies (the venv's dist-info, or
    an egg-info an editable install left beside its source), so no
    fetcher entry point; its module imports only from a PYTHONPATH
    entry (a test that puts packages/taxjson-fetch/src there, as CI's
    checkout allows), not through the editable install.

A child process inherits it only while its PYTHONPATH keeps DIR."""
import argparse
import sys
from pathlib import Path

TEMPLATE = r'''import os, site, sys, sysconfig
from importlib.machinery import PathFinder
_HIDDEN = @EXTRAS@.split()
_PLUGIN = @PLUGIN@
_SITE = [os.path.realpath(p) for p in {
    sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"],
    *getattr(site, "getsitepackages", lambda: [])(),
    getattr(site, "getusersitepackages", lambda: "")()} if p]
# A test's own stand-in: what PYTHONPATH names (this folder aside).
_HERE = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
_OWN = [os.path.realpath(p) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if p and os.path.realpath(p) != _HERE]
def _where(spec):
    return spec and (spec.origin if spec.has_location else
                     next(iter(spec.submodule_search_locations or ()), None))
def _under(path, roots):
    path = os.path.realpath(path)
    return any(path == r or path.startswith(r + os.sep) for r in roots)
class _HideExtras:
    """scripts/hide_optional.py: the optional extras and the broker-fetch
    plugin are not installed."""
    @staticmethod
    def find_spec(name, path=None, target=None):
        if _PLUGIN and (name == _PLUGIN or name.startswith(_PLUGIN + ".")):
            spec = PathFinder.find_spec(name, path)
            where = _where(spec)
            if where and not _under(where, _OWN):
                raise ModuleNotFoundError(f"No module named {name!r} (the "
                                          f"taxjson-fetch plugin, hidden)", name=name)
            return spec
        if not any(name == h or name.startswith(h + ".") for h in _HIDDEN):
            return None
        spec = PathFinder.find_spec(name, path)
        where = _where(spec)
        if where and _under(where, _SITE):
            raise ModuleNotFoundError(f"No module named {name!r} (an "
                                      f"optional extra, hidden)", name=name)
        return spec
sys.meta_path.insert(0, _HideExtras)
# No plugin distribution, wherever its metadata lies: a fetcher is found
# by its entry point (lib/fetchers.py), not by an import.
_find_distributions = PathFinder.find_distributions
def _dist_name(d):
    try:
        n = getattr(d, "_normalized_name", None) or d.metadata["Name"] or ""
    except Exception:
        return ""
    return n.lower().replace("-", "_").replace(".", "_")
def _without_plugin(*args, **kwargs):
    return (d for d in _find_distributions(*args, **kwargs)
            if _dist_name(d) != _PLUGIN)
if _PLUGIN:
    PathFinder.find_distributions = staticmethod(_without_plugin)
'''


def render(extras: str = "", plugin: str = "") -> str:
    """The sitecustomize.py text hiding `extras` (import names, blank
    separated) and the plugin `plugin` (its import name; "" for none)."""
    return (TEMPLATE.replace("@EXTRAS@", repr(" ".join(extras.split())))
            .replace("@PLUGIN@", repr(plugin.strip())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="scripts/hide_optional.py",
        description="Write DIR/sitecustomize.py hiding the optional "
                    "extras and/or the broker-fetch plugin.")
    ap.add_argument("dir", help="the folder to write sitecustomize.py in")
    ap.add_argument("--extras", default="",
                    help="import names of the extras to hide")
    ap.add_argument("--plugin", default="",
                    help="the plugin's import name (taxjson_fetch)")
    a = ap.parse_args(argv)
    if not a.extras.split() and not a.plugin.strip():
        ap.error("nothing to hide: give --extras, --plugin or both")
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "sitecustomize.py").write_text(render(a.extras, a.plugin),
                                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

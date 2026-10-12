"""Test support for the taxjson-fetch plugin's tests.

Imported FIRST by every test module. The tests run either against an
installed plugin (`pip install --no-deps -e packages/taxjson-fetch`) or straight
from a checkout (scripts/ci.sh: PYTHONPATH only). In the second case the
core's `taxjson fetch` cannot see the plugin — it finds fetchers by
entry point, which only an installed distribution declares — so this
module writes a throwaway `.dist-info` declaring the same entry point
and puts it on sys.path and PYTHONPATH (the CLI tests spawn
`python -m taxjson.bin.taxjson_run`). It also puts the plugin's src/,
the core's src/ and the core's tests/ (tax_rules markers) on the path
when they are not importable already.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent   # packages/taxjson-fetch
PLUGIN_SRC = PLUGIN_ROOT / "src" / "taxjson_fetch"
REPO_ROOT = PLUGIN_ROOT.parent.parent                    # the taxjson checkout
CORE_TESTS = REPO_ROOT / "tests"
ENTRY_POINT = "taxjson-fetch = taxjson_fetch.plugin:BrokerFetcher"


def _add_path(p: Path, *, env: bool = True) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
    if env:
        parts = [x for x in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                 if x]
        if s not in parts:
            os.environ["PYTHONPATH"] = os.pathsep.join([s] + parts)


def _registered() -> bool:
    from importlib import metadata
    try:
        eps = metadata.entry_points(group="taxjson.fetchers")
    except TypeError:                          # Python 3.9
        eps = metadata.entry_points().get("taxjson.fetchers", [])
    return any(ep.name == "taxjson-fetch" for ep in eps)


def _setup() -> None:
    try:
        import taxjson_fetch  # noqa: F401
    except ImportError:
        _add_path(PLUGIN_ROOT / "src")
    try:
        import taxjson  # noqa: F401
    except ImportError:
        _add_path(REPO_ROOT / "src")
    if CORE_TESTS.is_dir():
        # tax_rules (the @rule markers): in-process only, and appended so
        # this directory's own test modules win over same-named ones.
        if str(CORE_TESTS) not in sys.path:
            sys.path.append(str(CORE_TESTS))
    if not _registered():
        tmp = Path(tempfile.mkdtemp(prefix="taxjson-fetch-ep-"))
        atexit.register(shutil.rmtree, tmp, True)
        info = tmp / "taxjson_fetch_dev-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: taxjson-fetch-dev\n"
            "Version: 0.0.0\n", encoding="utf-8")
        (info / "entry_points.txt").write_text(
            f"[taxjson.fetchers]\n{ENTRY_POINT}\n", encoding="utf-8")
        _add_path(tmp)
    try:
        from taxjson.lib import fetchers
        fetchers.discover(refresh=True)
    except ImportError:
        pass


_setup()

"""`taxjson fetch` plugins: broker downloaders live outside the core.

The core holds no broker API client and never reads a broker credential.
`taxjson fetch` is a thin dispatcher: it finds the fetchers installed in
the same Python environment, picks the accounts each one serves, and
runs them. The Questrade REST API and IBKR Flex Web Service fetcher is
the separate distribution `taxjson-fetch` (packages/taxjson-fetch in the
repository); another broker can ship its own package the same way.

A fetcher registers under the entry-point group ``taxjson.fetchers``::

    [project.entry-points."taxjson.fetchers"]
    mybroker = "mybroker_fetch.plugin:Fetcher"

The entry point names a class (instantiated with no arguments) or a
ready object. It provides:

    brokerages    tuple of the `brokerage = "..."` values (under
                  [accounts.<name>] in taxjson.toml) it downloads for
    description   one line for `taxjson fetch --list`
    fetch(request) -> {account: {...}}
                  download every account in request.accounts into
                  inputs/<account>/ in a format an existing parser
                  reads; return a JSON-able result per account (the
                  `--json` document). Fail with SystemExit("taxjson
                  fetch: ...") — exit 1 — never a traceback.

and optionally:

    add_arguments(parser)  its own `taxjson fetch` options (an option
                  two installed fetchers both define is shared — keep
                  the conventional meaning, e.g. --year)
    account_keys  extra [accounts.<name>] keys it reads, so the config
                  check accepts them (brokerage, account and query_id
                  are always accepted)
    setup_hint    text shown when no account declares a brokerage it
                  serves (an example [accounts.<name>] table)

`request` (FetchRequest) carries the project root, work/ directory,
parsed taxjson.toml, the accounts to fetch (each declares one of the
fetcher's brokerages), the parsed arguments, a `say` progress printer
(stderr under --json), the --dry-run / --json flags, and where the
project's files are (lib/project_layout): `inputs` (the folder of
inputs/<account>/ — with `[settings] inputs_dir`, the exports folder
every year's project shares; `shared_inputs` says so) and `holdings`
(the year's positions snapshots folder, lib/holdings_dir). A fetcher
writes downloads under `inputs`, never root/"inputs" (an older core
sends no `inputs`: fall back to root/"inputs" then).
"""
from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ENTRY_POINT_GROUP = "taxjson.fetchers"
# Never a PyPI install by name: neither distribution is on PyPI yet,
# and the names there are not ours (security review H1).
from taxjson.lib.install_hint import INSTALLER as _INSTALLER  # noqa: E402
from taxjson.lib.install_hint import NOT_ON_PYPI as _NOT_ON_PYPI  # noqa: E402
# The installer installs taxjson-fetch by default; one that opted out
# (--without-fetch, remembered) takes it back with --with-fetch.
INSTALL_HINT = (f"install it by re-running the installer, which installs "
                f"taxjson-fetch by default (`{_INSTALLER}`; add "
                f"`_ --with-fetch` if it was installed --without-fetch), "
                f"or, from a checkout, `pip install --no-deps -e packages/taxjson-fetch` "
                f"into taxjson's own environment — {_NOT_ON_PYPI}")
# Keys the core always accepts under [accounts.<name>] for a fetcher
# (the ones taxjson-fetch reads), installed or not: a project that used
# `taxjson fetch` keeps validating when the plugin is absent.
CORE_ACCOUNT_KEYS = ("brokerage", "account", "query_id")


@dataclass
class FetchRequest:
    root: Path
    work: Path
    config: Dict[str, Any]
    accounts: List[str]
    args: argparse.Namespace
    say: Callable[[str], None]
    dry_run: bool = False
    json: bool = False
    # Where the project's files are (lib/project_layout): the inputs
    # folder (shared by every year's project when `shared_inputs`) and
    # the year's holdings folder. None: root/"inputs", root/"holdings".
    inputs: Optional[Path] = None
    holdings: Optional[Path] = None
    shared_inputs: bool = False


@dataclass
class Fetcher:
    """One installed fetcher: its entry-point name, the distribution
    that ships it, and the loaded object."""
    name: str
    dist: str
    impl: Any

    @property
    def brokerages(self) -> Tuple[str, ...]:
        return tuple(str(b) for b in
                     (getattr(self.impl, "brokerages", ()) or ()))

    @property
    def description(self) -> str:
        return str(getattr(self.impl, "description", "") or "")

    @property
    def account_keys(self) -> Tuple[str, ...]:
        return tuple(str(k) for k in
                     (getattr(self.impl, "account_keys", ()) or ()))

    @property
    def setup_hint(self) -> str:
        return str(getattr(self.impl, "setup_hint", "") or "")


_CACHE: Optional[Tuple[List[Fetcher], List[str]]] = None


def _entry_points() -> List[Any]:
    from importlib import metadata
    try:
        eps = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:                     # Python 3.9: a dict by group
        eps = metadata.entry_points().get(ENTRY_POINT_GROUP, [])
    return list(eps)


def discover(refresh: bool = False) -> Tuple[List[Fetcher], List[str]]:
    """(fetchers, problems): every fetcher installed in this
    environment, by entry-point name (the first of a duplicated name
    wins), and one line per entry point that failed to load."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    found: List[Fetcher] = []
    problems: List[str] = []
    seen = set()
    for ep in _entry_points():
        if ep.name in seen:
            continue
        seen.add(ep.name)
        dist = ""
        try:
            d = getattr(ep, "dist", None)
            if d is not None:
                dist = f"{d.metadata['Name']} {d.version}"
        except Exception:
            dist = ""
        try:
            obj = ep.load()
            impl = obj() if isinstance(obj, type) else obj
        except Exception as e:               # a broken plugin: named, skipped
            problems.append(f"fetcher {ep.name!r} failed to load "
                            f"({type(e).__name__}: {e})")
            continue
        if not callable(getattr(impl, "fetch", None)):
            problems.append(f"fetcher {ep.name!r} has no fetch(request) "
                            f"— skipped")
            continue
        found.append(Fetcher(ep.name, dist, impl))
    _CACHE = (found, problems)
    return _CACHE


def installed() -> List[Fetcher]:
    return discover()[0]


def served_brokerages(fetchers: Optional[List[Fetcher]] = None
                      ) -> Dict[str, Fetcher]:
    """{brokerage value: the fetcher that serves it} (first wins)."""
    out: Dict[str, Fetcher] = {}
    for f in (installed() if fetchers is None else fetchers):
        for b in f.brokerages:
            out.setdefault(b, f)
    return out


def declared_brokerages(cfg: Dict[str, Any]) -> Dict[str, str]:
    """{account: brokerage} for every [accounts.<name>] with a
    non-empty `brokerage` key, in config order."""
    out: Dict[str, str] = {}
    for name, ac in (cfg.get("accounts") or {}).items():
        b = str((ac or {}).get("brokerage") or "").strip() \
            if isinstance(ac, dict) else ""
        if b:
            out[str(name)] = b
    return out


def add_fetcher_arguments(parser: argparse.ArgumentParser) -> None:
    """Let each installed fetcher add its own options to `taxjson
    fetch`; one that fails is named on stderr and skipped (its options
    are then unknown, never a traceback)."""
    for f in installed():
        add = getattr(f.impl, "add_arguments", None)
        if not callable(add):
            continue
        try:
            add(parser)
        except Exception as e:
            emit_line(f"taxjson fetch: warning: fetcher {f.name!r} could not "
                      f"add its options ({type(e).__name__}: {e})",
                      file=sys.stderr)


def listing() -> List[Dict[str, Any]]:
    """The `fetch --list` rows."""
    return [{"name": f.name, "distribution": f.dist,
             "brokerages": list(f.brokerages),
             "description": f.description} for f in installed()]

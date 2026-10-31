"""Exchange rates a test plants in a project's work/to_base.csv, put in
the HOME's currency cache as well, so a run that rebuilds the file
writes the same rates back (backlog TJ-0635, TJ-0606).

`tjs run` rebuilds work/to_base.csv when a taxjson source file is
newer than it (stage_currency_rates, needs_rebuild: a file edited while
the suite runs) or when its rates end more than three days ago, and an
offline rebuild reads ~/.currency_price_cache.json only. A test's own
empty HOME then gives the run no rate at all ("no exchange rate"), and
the suite's synthetic HOME (tests/_hermetic) its synthetic ones, so the
figures change. mirror() writes the planted rows into that cache: the
Bank of Canada series for a CAD target the Bank publishes, Yahoo's
otherwise, each with the coverage the rebuild checks.

    plant(root / "work" / "to_base.csv", text, home)    # the test's HOME
    plant(path, text, cleanup=self.addCleanup)          # the suite's HOME

The suite's synthetic HOME is shared by every test of the process (and
by every module of a `unittest discover` run), so a mirror into it needs
a `cleanup` (self.addCleanup, cls.addClassCleanup) that puts the cache
back as it was.
"""
import json
import os
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from taxjson.bin import to_base_curr as TB

CACHE = ".currency_price_cache.json"

Row = Tuple[str, str, str, str]


def rows(text: str) -> List[Row]:
    """(date, from, to, rate text) of each line of a rates file
    (`YYYY-MM-DD HH:MM:SS FROM TO RATE SOURCE [PUBLISHED]`)."""
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 5:
            out.append((p[0], p[2].upper(), p[3].upper(), p[4]))
    return out


def merge_into(cache: dict, planted: Iterable[Row]) -> dict:
    """`cache` with every planted row as the observation of its date (a
    planted rate wins over the one the cache held for that date) and the
    planted dates in the coverage, as taxjson-to-base-curr reads them
    (resolve_rows)."""
    spans = {}
    for d, src, dst, rate in planted:
        pair = f"{src}{dst}"
        if dst == "CAD" and src in TB.BOC_CURRENCIES and d >= TB.BOC_START:
            blk = cache.setdefault("_boc", {}).setdefault(pair, {})
            blk.setdefault("obs", {})[d] = rate
            key = f"boc:{pair}"
        else:
            cache[f"{pair}-{d}"] = float(rate)
            key = f"yahoo:{pair}"
        spans.setdefault(key, []).append([d, d])
    cov = cache.setdefault("_coverage", {})
    for key, days in spans.items():
        cov[key] = TB._merge([list(iv) for iv in cov.get(key) or []] + days)
    return cache


def _shared(home: Path) -> bool:
    here = os.environ.get("HOME")
    return bool(here) and Path(here).resolve() == home.resolve()


def mirror(rates_csv, home=None, *,
           cleanup: Optional[Callable] = None) -> None:
    """Put the rates of `rates_csv` in `home`'s currency cache (default:
    this process's HOME, the suite's synthetic one, which needs
    `cleanup`)."""
    home = Path(home if home is not None else os.environ["HOME"])
    if _shared(home):
        if cleanup is None:
            raise TypeError(f"{home} is this process's HOME, shared by the "
                            "other tests: pass cleanup= (self.addCleanup "
                            "or cls.addClassCleanup) to put it back")
        cleanup(_restorer(home / CACHE))
    p = home / CACHE
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    home.mkdir(parents=True, exist_ok=True)
    text = Path(rates_csv).read_text(encoding="utf-8")
    p.write_text(json.dumps(merge_into(cache, rows(text))), encoding="utf-8")


def _restorer(p: Path) -> Callable[[], None]:
    try:
        before: Optional[bytes] = p.read_bytes()
    except OSError:
        before = None

    def restore():
        if before is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(before)
    return restore


def plant(path, text: str, home=None, *,
          cleanup: Optional[Callable] = None) -> Path:
    """Write `text` (a rates file) to `path`, a project's
    work/to_base.csv, and mirror() it into `home`'s cache."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mirror(path, home, cleanup=cleanup)
    return path

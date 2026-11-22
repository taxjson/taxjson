"""The market data taxjson ships (taxjson/data/markets.toml) and the
listing suffixes it names: below lib/markets, which adds a project's
ticker.map market lines to it, and lib/ticker_map, which checks a VENUE
line's suffix against it (step 8 of the architecture plan: out of
lib/markets, which ticker_map called up into while markets reads the
map's lines)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from taxjson.project.toml_io import read_toml

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "markets.toml"

_DATA: Optional[Dict[str, Any]] = None


class MarketsDataError(RuntimeError):
    """The shipped data file is missing or unreadable (a broken
    install)."""


def data() -> Dict[str, Any]:
    """The parsed shipped defaults (cached)."""
    global _DATA
    if _DATA is None:
        try:
            _DATA = read_toml(DATA_FILE)
        except (OSError, ValueError) as e:
            raise MarketsDataError(
                f"taxjson's market data file {DATA_FILE} cannot be read "
                f"({e}) — reinstall taxjson") from None
    return _DATA


def _venues() -> Dict[str, Dict[str, str]]:
    return data()["venues"]


@lru_cache(maxsize=None)
def known_suffixes() -> FrozenSet[str]:
    """Every listing suffix the books know (TO V CN NE VN US L AX)."""
    return frozenset(_venues())


def suffix_of(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s.rsplit(".", 1)[-1] if "." in s else ""


@lru_cache(maxsize=None)
def canadian_suffixes() -> FrozenSet[str]:
    """The listing suffixes of Canadian exchanges (TO V CN NE VN)."""
    return frozenset(s for s, v in _venues().items()
                     if v.get("country") == "CAN")


_SUFFIX_RE_CACHE: Dict[FrozenSet[str], Any] = {}


def listing_suffix_re(suffixes=None):
    """A compiled `\\.(SUFFIX|...)$` (any case) for `suffixes` (default:
    every known listing suffix), longest alternative first."""
    import re
    key = frozenset(s.upper() for s in (suffixes if suffixes is not None
                                        else known_suffixes()))
    rx = _SUFFIX_RE_CACHE.get(key)
    if rx is None:
        alt = "|".join(sorted(key, key=lambda s: (-len(s), s)))
        rx = re.compile(rf"\.({alt})$", re.IGNORECASE)
        _SUFFIX_RE_CACHE[key] = rx
    return rx

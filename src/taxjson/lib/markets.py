"""Market reference data: the ONE home for venue suffixes, currencies and
the security lists taxjson cannot read from a broker export.

The shipped defaults live in ``taxjson/data/markets.toml`` (package
data, clearly labelled there). A project's ticker.map extends or
overrides them one symbol at a time (lib/ticker_map side rules):

    STABLE      SYMBOL USD|NO     a US-dollar stablecoin (or: not one)
    SPLITSHARE  ROOT [YES|NO]     a Canadian split-share corporation
    INDEXOPT    ROOT [YES|NO]     a US broad-based index option root
    EVENING     ROOT [YES|NO]     an option root with a Cboe evening session
    MULT        SYMBOL N          an option's contract size
    VENUE       IBCODE SUFFIX|NO  an IB listing exchange and its suffix
    GLOBAL      CODE SYMBOL       (a bare crypto code) folded before parsing

Which ticker.map: the one `use_ticker_map(path)` named (a tool's
--ticker-map), else the TAXJSON_TICKER_MAP environment variable, which
`taxjson` sets for every command and stage of a project (empty = the
project has none). Nothing here is country-specific: the callers gate
(§1256 is US-only, split-share corporations Canada-only — lib/country).

Whenever a BUILT-IN list (not a ticker.map line) decides an outcome the
caller says so once per symbol (`note_builtin`), naming the ticker.map
line that would change it.
"""
from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple

ENV_TICKER_MAP = "TAXJSON_TICKER_MAP"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "markets.toml"

_DATA: Optional[Dict[str, Any]] = None
_EXPLICIT_MAP: Optional[str] = None
_OVR_CACHE: Dict[Tuple[str, float], "Overrides"] = {}
_NOTED: set = set()


class MarketsDataError(RuntimeError):
    """The shipped data file is missing or unreadable (a broken
    install)."""


def data() -> Dict[str, Any]:
    """The parsed shipped defaults (cached)."""
    global _DATA
    if _DATA is None:
        from taxjson.lib.tomlcompat import tomllib
        try:
            _DATA = tomllib.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise MarketsDataError(
                f"taxjson's market data file {DATA_FILE} cannot be read "
                f"({e}) — reinstall taxjson") from None
    return _DATA


@lru_cache(maxsize=None)
def _list(name: str) -> FrozenSet[str]:
    return frozenset(str(x).upper() for x in data()["lists"][name])


# ------------------------------------------------------------ overrides

class Overrides:
    """The market lines of one ticker.map: {key: value}; a value of
    False / None removes a built-in entry."""

    def __init__(self) -> None:
        self.stable: Dict[str, bool] = {}
        self.splitshare: Dict[str, bool] = {}
        self.indexopt: Dict[str, bool] = {}
        self.evening: Dict[str, bool] = {}
        self.mult: Dict[str, float] = {}
        self.venue: Dict[str, Optional[str]] = {}
        self.alias: Dict[str, str] = {}
        self.path: Optional[str] = None


_EMPTY = Overrides()


def use_ticker_map(path) -> None:
    """Read market overrides from the ticker.map at `path` (a tool's
    --ticker-map); None goes back to TAXJSON_TICKER_MAP."""
    global _EXPLICIT_MAP
    _EXPLICIT_MAP = str(path) if path else None


def ticker_map_path() -> Optional[str]:
    if _EXPLICIT_MAP:
        return _EXPLICIT_MAP
    p = (os.environ.get(ENV_TICKER_MAP) or "").strip()
    return p or None


def overrides() -> Overrides:
    """The current ticker.map's market lines (an empty set when there is
    no map or it cannot be read — `taxjson run` refuses a bad map up
    front and names the line)."""
    p = ticker_map_path()
    if not p:
        return _EMPTY
    try:
        mtime = os.stat(p).st_mtime
    except OSError:
        return _EMPTY
    key = (p, mtime)
    hit = _OVR_CACHE.get(key)
    if hit is not None:
        return hit
    from taxjson.lib.ticker_map import read_side_rules
    o = Overrides()
    o.path = p
    try:
        r = read_side_rules(p)
    except Exception:   # unreadable: the run refuses it with the reason
        r = None
    if r is not None:
        o.stable = dict(r.stable)
        o.splitshare = dict(r.splitshare)
        o.indexopt = dict(r.indexopt)
        o.evening = dict(r.evening)
        o.mult = dict(r.mult)
        o.venue = dict(r.venue)
        o.alias = _bare_globals(p)
    _OVR_CACHE.clear()
    _OVR_CACHE[key] = o
    return o


def _bare_globals(path: str) -> Dict[str, str]:
    """ticker.map GLOBAL lines whose both sides are bare codes (no
    listing suffix): crypto asset codes, applied by the crypto parsers
    BEFORE they classify a row (a staked-coin code folded into its
    coin is then a wallet move, not a swap)."""
    out: Dict[str, str] = {}
    try:
        from taxjson.lib.cli_diag import read_text_utf8
        text = read_text_utf8(Path(path))
    except Exception:
        return out
    for raw in text.splitlines():
        line = raw.lstrip("﻿").split("#", 1)[0].split()
        if len(line) == 3 and line[0].upper() == "GLOBAL" \
                and "." not in line[1] and "." not in line[2]:
            out[line[1].upper()] = line[2].upper()
    return out


# ---------------------------------------------------------------- notes

def note_builtin(kind: str, key: str, message: str, *,
                 rollup: Optional[Tuple[str, str]] = None) -> None:
    """Print `message` once per (kind, key) per process, as a `note:`
    line on stderr (a stage's DIAGNOSTICS).

    `rollup` = (label, ...) rolls the notes of one `kind` up for a
    person: shown to one (stderr is the process's own stream and wraps,
    docs/output-style.md), the process prints ONE note at exit naming
    every key's `label` instead of a line per key (_ROLLUPS words it).
    Captured for a program (width 0: a stage's .diag, the .sum
    DIAGNOSTICS, the checklist) or redirected by an in-process caller
    (redirect_stderr into a buffer it reads or discards, as wash_radar
    and t1135 do) each key keeps its own line, written at once to the
    current stderr, byte for byte as before (the stage_msg._captured
    rule)."""
    k = (kind, key, ticker_map_path())
    if k in _NOTED:
        return
    _NOTED.add(k)
    from taxjson.lib import out
    if rollup is not None and kind in _ROLLUPS and _shown_to_a_person():
        global _AT_EXIT
        if not _AT_EXIT:
            import atexit
            atexit.register(flush_notes)
            _AT_EXIT = True
        _ROLLED.setdefault(kind, []).append(rollup[0])
        return
    # Wrapped at the house width for a person; one line when captured
    # for a program (a stage's .diag), as before (docs/output-style.md).
    out.note(message)


# kind -> (headline(n), advice): the one note a rolled-up kind prints.
_ROLLUPS = {
    "mult": (lambda n: f"{n} option root(s) whose export does not state "
             f"the contract size: 100 shares per contract (or the size "
             f"shown) is ASSUMED where it matters (an exercise or "
             f"assignment, replacement shares)",
             "For a mini or an adjusted series add `MULT <ROOT> N` to "
             "ticker.map."),
}
# kind -> one-line form for a few labels.
_ROLLUP_LINE = {
    "mult": (lambda names: f"{names}: 100 shares per option contract "
             f"ASSUMED — `MULT <ROOT> N` in ticker.map if not"),
}
_ROLLED: Dict[str, list] = {}
# flush_notes is registered with atexit once per process.
_AT_EXIT = False


def _shown_to_a_person() -> bool:
    """stderr is the process's real stream and wraps (not width 0): the
    stage_msg._captured rule. A redirected sys.stderr (a buffer an
    in-process caller reads or discards) is captured."""
    from taxjson.lib import out
    return (out.real_stream(sys.stderr) is sys.__stderr__
            and out.width(sys.stderr) > 0)


def flush_notes(file=None) -> None:
    """Print the rolled-up notes (note_builtin(..., rollup=...)) and
    forget them: one note per kind, its labels sorted and wrapped."""
    from taxjson.lib import out
    for kind in sorted(_ROLLED):
        labels = sorted(set(_ROLLED[kind]))
        if kind in _ROLLUP_LINE and len(labels) <= 3:
            # One line (docs/output-style.md, Essentials first).
            out.note(_ROLLUP_LINE[kind](", ".join(labels)), file=file)
            continue
        head, advice = _ROLLUPS[kind]
        out.note(head(len(labels)), details=[", ".join(labels) + ".",
                                             advice],
                 file=file)
    _ROLLED.clear()


def reset_notes() -> None:
    _NOTED.clear()
    _ROLLED.clear()


def _decide(kind: str, key: str, ovr: Dict[str, bool],
            builtin: FrozenSet[str], keyword: str, what: str,
            note: bool) -> bool:
    """A yes/no list lookup: the ticker.map line wins; else the built-in
    list, noted once when it says yes."""
    if key in ovr:
        return bool(ovr[key])
    hit = key in builtin
    if hit and note:
        note_builtin(kind, key, (
            f"{key} is treated as {what} by taxjson's built-in market list "
            f"(taxjson/data/markets.toml); if that is wrong add "
            f"`{keyword} {key} NO` to ticker.map."))
    return hit


# --------------------------------------------------------------- venues

def _venues() -> Dict[str, Dict[str, str]]:
    return data()["venues"]


@lru_cache(maxsize=None)
def known_suffixes() -> FrozenSet[str]:
    """Every listing suffix the books know (TO V CN NE VN US L AX)."""
    return frozenset(_venues())


@lru_cache(maxsize=None)
def receipt_suffixes() -> FrozenSet[str]:
    """The listing suffixes of venues that list depositary receipts
    under the underlying's ticker (`receipts = true`: NE, Cboe Canada's
    CDRs)."""
    return frozenset(s for s, v in _venues().items() if v.get("receipts"))


def receipt_words() -> FrozenSet[str]:
    """The name words of a depositary receipt ([lists] receipt_words, as
    symbol_codes.exact_name spells them)."""
    return _list("receipt_words")


@lru_cache(maxsize=None)
def canadian_suffixes() -> FrozenSet[str]:
    """The listing suffixes of Canadian exchanges (TO V CN NE VN)."""
    return frozenset(s for s, v in _venues().items()
                     if v.get("country") == "CAN")


def suffix_of(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    return s.rsplit(".", 1)[-1] if "." in s else ""


def is_canadian_listing(symbol: str) -> bool:
    return suffix_of(symbol) in canadian_suffixes()


def suffix_currency(suffix: str) -> Optional[str]:
    v = _venues().get(str(suffix or "").upper())
    return v.get("currency") if v else None


def yahoo_suffix(suffix: str) -> Optional[str]:
    """Yahoo Finance's suffix for a book listing suffix ("" = bare)."""
    s = str(suffix or "").upper()
    v = _venues().get(s)
    return None if v is None else v.get("yahoo", s)


def suffix_country(suffix: str) -> Optional[str]:
    """ISO 3166 alpha-3 country of a listing suffix's exchange."""
    v = _venues().get(str(suffix or "").upper())
    return v.get("country") if v else None


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


def strip_listing_suffix(symbol: str, suffixes=None) -> str:
    """`symbol` without a trailing listing suffix (default: any known
    one): ZZQ.B.TO -> ZZQ.B, ZZQ.VN -> ZZQ; ZZQ.B stays."""
    return listing_suffix_re(suffixes).sub("", str(symbol or ""))


def currency_suffixes() -> Dict[str, str]:
    """{currency: listing suffix} for a row that names only its
    currency."""
    return dict(data()["currency_suffix"])


def currency_suffix(currency: str) -> Optional[str]:
    return data()["currency_suffix"].get(str(currency or "").upper())


def isin_country_suffix(cc: str) -> Optional[str]:
    return data()["isin_country_suffix"].get(str(cc or "").upper())


def ib_venue_suffix(code: str) -> Optional[str]:
    """The listing suffix of an IB "Listing Exch" code (VENUE lines
    first); "" for a venue a `VENUE CODE NO` line removed, None for an
    unknown venue."""
    c = str(code or "").strip().upper()
    o = overrides().venue
    if c in o:
        return o[c] or ""
    return data()["ib_venues"].get(c)


# A description naming a fund's US-dollar units or class ("... U S DLR
# CURRENCY ETF", "... USD UNITS"): vocabulary, not security data.
USD_UNITS_RE = re.compile(
    r'\b(?:U\.?\s?S\.?\s+(?:DOLLAR|DLR)|USD)\s+'
    r'(?:UNITS?|CLASS|SERIES|CURRENCY)\b', re.I)


def usd_unit_listing(root: str) -> str:
    """The book symbol of a Canadian-listed fund's US-dollar unit class
    ([usd_unit_class]: ROOT.U.TO), from a root that may already carry
    the class or a suffix (QZD, QZD.U, QZD.US, QZD.U.TO)."""
    conv = data()["usd_unit_class"]
    cls, sfx = str(conv["class"]).upper(), str(conv["suffix"]).upper()
    r = strip_listing_suffix(str(root or "").strip().upper())
    if r.endswith(f".{cls}"):
        r = r[:-len(cls) - 1]
    return f"{r}.{cls}.{sfx}"


# ------------------------------------------------------------ currencies

def fiat_currencies() -> FrozenSet[str]:
    """The fiat currencies (ISO 4217) an exchange row can be quoted in."""
    return _list("fiat")


def usd_stablecoins() -> FrozenSet[str]:
    """The US-dollar stablecoins: the built-in list with ticker.map
    STABLE lines applied."""
    base = set(_list("usd_stablecoins"))
    for k, v in overrides().stable.items():
        (base.add if v else base.discard)(k)
    return frozenset(base)


def builtin_usd_stablecoins() -> FrozenSet[str]:
    return _list("usd_stablecoins")


def is_usd_stablecoin(symbol: str, note: bool = False) -> bool:
    return _decide("stable", str(symbol or "").strip().upper(),
                   overrides().stable, _list("usd_stablecoins"), "STABLE",
                   "a US-dollar stablecoin", note)


# --------------------------------------------------------- security lists

def split_share_roots() -> FrozenSet[str]:
    base = set(_list("split_share_corporations"))
    for k, v in overrides().splitshare.items():
        (base.add if v else base.discard)(k)
    return frozenset(base)


def is_split_share_root(root: str, note: bool = True) -> bool:
    return _decide("splitshare", str(root or "").strip().upper(),
                   overrides().splitshare, _list("split_share_corporations"),
                   "SPLITSHARE", "a split-share corporation", note)


def index_option_roots() -> FrozenSet[str]:
    base = set(_list("us_1256_index_option_roots"))
    for k, v in overrides().indexopt.items():
        (base.add if v else base.discard)(k)
    return frozenset(base)


def is_index_option_root(root: str, note: bool = True) -> bool:
    return _decide("indexopt", str(root or "").strip().upper(),
                   overrides().indexopt,
                   _list("us_1256_index_option_roots"), "INDEXOPT",
                   "a broad-based index option (§1256)", note)


def evening_session_roots() -> FrozenSet[str]:
    base = set(_list("cboe_evening_session_roots"))
    for k, v in overrides().evening.items():
        (base.add if v else base.discard)(k)
    return frozenset(base)


def is_evening_session_root(root: str, note: bool = True) -> bool:
    return _decide("evening", str(root or "").strip().upper(),
                   overrides().evening, _list("cboe_evening_session_roots"),
                   "EVENING", "an option with a Cboe evening session (a "
                   "20:15 ET onward fill trades on the next trading day)",
                   note)


def contract_size(symbol: str, root: str = "") -> Optional[float]:
    """The ticker.map MULT contract size of an option `symbol` (exact
    symbol first, then its root), None when no line names it."""
    o = overrides().mult
    for k in (str(symbol or "").strip().upper(),
              str(root or "").strip().upper()):
        if k and k in o:
            return o[k]
    return None


# ------------------------------------------------------------ crypto codes

def kraken_assets() -> Dict[str, str]:
    """Kraken's legacy asset codes → common ticker (built-in)."""
    return dict(data()["kraken_assets"])


def crypto_alias(code: str) -> str:
    """A bare crypto asset code after the project's ticker.map GLOBAL
    lines (none: unchanged)."""
    c = str(code or "").strip().upper()
    return overrides().alias.get(c, c)

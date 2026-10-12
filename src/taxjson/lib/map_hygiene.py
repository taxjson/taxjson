"""ticker.map hygiene read from the last run's files, for `taxjson
ticker-map --suggest`: the listing pairs the map does not answer
(map_gaps, MAP-GAP: "verify"), the rules no symbol of the books reaches
("unused rule, delete?"), and the listing helpers `taxjson tips` shares
(a symbol's root and listing suffix, the listings the books show, the
security names the exports give each listing, and what those names say
about a US and a Canadian listing of one root).

A data check, not a tax rule (both countries). Nothing here writes.

MAP-GAP (map_gaps): a .US and a Canadian listing of one root, both seen
in the project (the per-listing holdings reports, which are built before
TOBASE; the dividend history; the map's own renames — an option counts
for its underlying), with no GLOBAL/TOBASE/JOURNAL or DISTINCT line for
them, and not joined by the last run itself (its effective map) —
unless the exports show the two apart (the Canadian line a depositary
receipt, or names of different companies: pair_verdict).
Interlisted shares usually keep their letters, so the pair is a
candidate to VERIFY, never a line the evidence proves: the reason says
whether the names agree, differ in form, or were not compared, and
names both answers (`TOBASE` if one security, `DISTINCT` if two).

Unused rules (unused_rules) are judged the way the ENGINE applies the
map (taxjson_ticker_map.map_symbol): a rule's FROM must equal a symbol
of the parsed sources exactly, or an option's underlying — ROOT-aware
on purpose: a rule with no stock rows is still live through OPTION
trades (ABC271217C00050000.US needs `TOBASE ABC.US ABC.TO`; a root-blind
check once pruned ten live rules). A rule reached through another
rule's target (a rename chain) is live too (R1-139). A suffix-less FROM
(`GLOBAL QQOL QQNW`) matches only a suffix-less symbol — the engine
never applies it to QQOL.US, so it is not live there either (S053-12);
the record says to write the suffixed form. A parsed source that cannot
be read skips the whole check (its symbols are unknown: calling a rule
unused then would invite pruning a live one, S042-10).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from taxjson.lib import project_layout as _PL


def symbol_root(sym: str) -> Tuple[str, str]:
    """(root, suffix) of a US or Canadian listing (every Canadian venue:
    lib/markets), else (symbol, '')."""
    from taxjson.lib.markets import canadian_suffixes
    parts = str(sym or "").rsplit(".", 1)
    if len(parts) == 2 and (parts[1].upper() == "US"
                            or parts[1].upper() in canadian_suffixes()):
        return parts[0].upper(), parts[1].upper()
    return str(sym or "").upper(), ""


def listing_names(cache: Path, accounts: List[str], glob: Dict[str, str]
                  ) -> Tuple[Dict[str, set], Dict[tuple, str]]:
    """({listing: its security names, symbol_codes.exact_name}, each
    name as written) from the parsed exports in work/
    (cross_listings.gather, the names the cross-listing join and the
    loss radar compare). A symbol a GLOBAL line renames gives its names
    to the target, the spelling the books carry; a TOBASE line moves
    none — each listing keeps its own."""
    from taxjson.lib import cross_listings as XL
    _legs, names, shown = XL.gather(cache, accounts)
    out: Dict[str, set] = {}
    for sym, ns in names.items():
        out.setdefault(glob.get(sym, sym), set()).update(ns)
    return out, shown


# pair_verdict kinds that show two listings are NOT one security.
APART = ("receipt", "different")


def pair_verdict(us: str, ca: str, names: Dict[str, set],
                 shown: Dict[tuple, str],
                 receipts: Optional[Dict[frozenset, str]] = None
                 ) -> Tuple[str, str]:
    """What the exports say about a US and a Canadian listing that share
    a root: (kind, text). A shared root is a candidate (many interlisted
    shares keep their letters) unless something shows the two apart:
    "receipt" — the Canadian line is a depositary receipt (a receipt
    word of markets.toml [lists] receipt_words in its name, or a listing
    on a receipt venue; or, given `receipts` — a Canadian project's
    lib/tobase_map.receipt_pairs — a CDR the interlisted master knows);
    "different" — the names name different
    companies (no leading company word in common): cross_listings.
    shown_apart. Otherwise "same" (equal names under the cross-listing
    join's rule, _names_verdict over symbol_codes.exact_name; text: the
    name as written), "unequal" (the names are not equal word for word;
    text: why) or "unknown" (no name for a side; text: which)."""
    from taxjson.lib import cross_listings as XL
    nu, nc = names.get(us, set()), names.get(ca, set())
    apart = XL.shown_apart(us, ca, names, receipts)
    if apart:
        return ("different" if apart == XL.DIFFERENT else "receipt"), apart
    why = XL._names_verdict(nu, nc, shown)
    if not nu or not nc:
        return "unknown", ("no security name for "
                           + ("either listing" if not nu and not nc
                              else (us if not nu else ca)))
    if why:
        return "unequal", why
    common = sorted(nu & nc)
    return "same", shown.get(common[0], " ".join(common[0]))


def sightings(symbols: Iterable[str]) -> Dict[str, Set[str]]:
    """{root: the listing suffixes seen} of US and Canadian listings
    among `symbols` (an option is a sighting of its underlying: a pair
    evidenced on one side only by options was missed, S042-04)."""
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    out: Dict[str, Set[str]] = {}
    for sym in symbols:
        sym = str(sym or "")
        if is_option_symbol(sym):
            sym = parse_option_underlying(sym) or sym
        r, suf = symbol_root(sym)
        if suf:
            out.setdefault(r, set()).add(suf)
    return out


@dataclass
class MapGap:
    """A US and a Canadian listing of one root the map does not answer."""
    us: str
    ca: str
    verdict: str        # pair_verdict kind: same | unequal | unknown
    tobase: str         # `TOBASE FROM TO` (tobase_direction)
    distinct: str       # `DISTINCT US CA`
    reason: str


def gap_reason(us: str, ca: str, verdict: str, what: str,
               tobase: str, distinct: str) -> str:
    """The MAP-GAP sentence: what the names show, then both answers."""
    lines = (f"`{tobase}` (one cost pool, and the loss rules see both); "
             f"if not, `{distinct}`.")
    if verdict == "same":
        return (f"{us} and {ca} carry the same name ({what!r}) but "
                f"ticker.map does not join them — if they are one "
                f"security add {lines}")
    if verdict == "unequal":
        return (f"{us} and {ca} share their letters but {what} — verify, "
                f"then if they are one security add {lines}")
    return (f"{us} and {ca} share their letters; names not compared "
            f"({what}) — verify, then if they are one security add "
            f"{lines}")


def own_renames(path: Path) -> Dict[str, str]:
    """The base-stage renames a map file's OWN lines make (upper-cased):
    a ticker.map without its tobase.map overlay, the run's effective map
    without the overlay section (lib/tobase_map.own_map_text). The
    sightings of a cross-listing come from the books and these only: a
    tobase.map pair (and its target) is the master's, not evidence that
    the books hold that listing (a TSX Venture junior sharing a US
    company's letters is no pair to verify). {} when unreadable."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_text, merge_renames
    from taxjson.lib.cli_diag import read_text_utf8
    from taxjson.lib.tobase_map import own_map_text
    try:
        text = own_map_text(read_text_utf8(Path(path)))
        tmap = _parse_map_text(text, Path(path).name)[0]
        return {str(k).upper(): str(v).upper()
                for k, v in merge_renames(tmap, to_base=True).items()}
    except Exception:                                   # noqa: BLE001
        return {}


def _map_view(root: Path) -> Tuple[Dict[str, str], Dict[str, str],
                                   Set[frozenset]]:
    """(the base-stage renames: ticker.map's, plus those of the last
    run's effective map — the joins the run made itself
    (cross_listings), each said by a Warning naming the DISTINCT line
    that undoes it —, ticker.map's GLOBAL renames, its DISTINCT pairs),
    upper-cased ({} for a map that cannot be read: `taxjson run`
    refuses it). Read quietly: a listing's JSON stays JSON."""
    from taxjson.bin.taxjson_ticker_map import _parse_map_file, merge_renames
    from taxjson.lib.cross_listings import EFFECTIVE_MAP
    root = Path(root)
    base: Dict[str, str] = {}
    glob: Dict[str, str] = {}
    distinct: Set[frozenset] = set()
    up = lambda d: {str(k).upper(): str(v).upper()      # noqa: E731
                    for k, v in d.items()}
    eff = root / "work" / EFFECTIVE_MAP
    if eff.is_file():
        try:
            base.update(up(merge_renames(_parse_map_file(eff)[0],
                                         to_base=True)))
        except Exception:                               # noqa: BLE001
            pass
    tm_path = _PL.ticker_map_path(root)
    if tm_path.is_file():
        try:
            tmap = _parse_map_file(tm_path)[0]
            base.update(up(merge_renames(tmap, to_base=True)))
            glob = up(merge_renames(tmap, to_base=False))
            distinct = {frozenset(str(x).upper() for x in pair)
                        for pair in getattr(tmap, "distinct", ()) or ()}
        except Exception:                               # noqa: BLE001
            pass
    return base, glob, distinct


def books_listings(root: Path, accounts: Iterable[str]
                   ) -> Tuple[Set[str], List[str]]:
    """(the symbols held in each account's per-listing holdings report
    and paid a dividend in its raw book, the files that could not be
    read). The holdings report is built before TOBASE, so the listing
    actually held is visible."""
    from taxjson.lib.tomlcompat import tomllib
    root = Path(root)
    syms: Set[str] = set()
    unread: List[str] = []
    for acct in accounts:
        f = root / "reports" / f"{acct}_holdings.toml"
        if f.is_file() and tomllib is not None:
            try:
                rows = tomllib.loads(f.read_text(encoding="utf-8")).get(
                    "holding", [])
                if not isinstance(rows, list):
                    raise ValueError("`holding` is not a list")
                syms.update(str(h.get("symbol") or "").upper()
                            for h in rows if isinstance(h, dict))
            except (OSError, ValueError, UnicodeDecodeError) as e:
                unread.append(f"reports/{f.name} ({e})")
        f = root / "work" / f"{acct}_raw.json"
        if f.is_file():
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                rows = doc.get("transactions") if isinstance(doc, dict) \
                    else None
                for t in rows if isinstance(rows, list) else []:
                    if isinstance(t, dict) and t.get("action") in (
                            "DIVIDEND", "DIVIDEND_IN_LIEU"):
                        syms.add(str(t.get("symbol") or "").upper())
            except (OSError, ValueError, RecursionError) as e:
                unread.append(f"work/{f.name} ({e})")
    syms.discard("")
    return syms, unread


def map_gaps(root: Path) -> Tuple[List[MapGap], List[str]]:
    """(every MAP-GAP of the project — module docstring —, the files
    that could not be read)."""
    from taxjson.lib.cross_listings import tobase_direction
    from taxjson.lib.markets import canadian_suffixes
    root = Path(root)
    cfg = _config(root)
    accts = cfg.get("accounts") if isinstance(cfg.get("accounts"),
                                              dict) else {}
    equity = [str(n) for n, a in accts.items()
              if not (isinstance(a, dict) and a.get("crypto"))]
    base_ccy = str(((cfg.get("settings") or {}).get("base_currency")
                    or "")).upper() or None
    renames, glob, distinct = _map_view(root)
    syms, unread = books_listings(root, equity)
    # Sightings: the books and the map's own lines (own_renames), never
    # a tobase.map pair or its target.
    from taxjson.lib.cross_listings import EFFECTIVE_MAP
    own: Dict[str, str] = {}
    for f in (root / "work" / EFFECTIVE_MAP, _PL.ticker_map_path(root)):
        if f.is_file():
            own.update(own_renames(f))
    seen = sightings(list(syms) + list(own) + list(own.values()))
    ca_sufs = canadian_suffixes()
    names: Optional[Tuple[Dict[str, set], Dict[tuple, str]]] = None
    # A CDR and its US share (the interlisted master, Canada): apart.
    from taxjson.lib.country import CountryError, settings_country
    from taxjson.lib.tobase_map import receipt_pairs
    try:
        _canada = settings_country(cfg.get("settings") or {}) == "canada"
    except CountryError:
        _canada = False
    receipts = receipt_pairs(_canada)
    out: List[MapGap] = []
    for rt in sorted(seen):
        sufs = seen[rt]
        if "US" not in sufs:
            continue
        for cs in sorted(x for x in sufs if x in ca_sufs):
            us, ca = f"{rt}.US", f"{rt}.{cs}"
            if frozenset((us, ca)) in distinct:
                continue            # the user's DISTINCT ruling
            if us in renames or ca in renames:
                continue
            if names is None:
                names = listing_names(root / "work", equity, glob)
            verdict, what = pair_verdict(us, ca, names[0], names[1],
                                         receipts)
            if verdict in APART:
                continue
            frm, to = tobase_direction(us, ca, base_ccy)
            tob, dis = f"TOBASE {frm} {to}", f"DISTINCT {us} {ca}"
            out.append(MapGap(us, ca, verdict, tob, dis,
                              gap_reason(us, ca, verdict, what, tob, dis)))
    return out, unread


def _config(root: Path) -> Dict[str, Any]:
    try:
        cfg = _PL.read_config_soft(root)
    except (OSError, ValueError, UnicodeDecodeError):
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _accounts(cfg: Dict[str, Any]) -> List[str]:
    accts = cfg.get("accounts")
    return [str(a) for a in accts] if isinstance(accts, dict) else []


@dataclass
class UnusedRule:
    """A ticker.map rename rule no symbol of the books reaches."""
    keyword: str        # GLOBAL | TOBASE | JOURNAL
    frm: str
    to: str
    hint: str = ""      # a suffix-less FROM: the suffixed form to write

    @property
    def line(self) -> str:
        return f"{self.keyword} {self.frm} {self.to}"

    @property
    def reason(self) -> str:
        return (f"no symbol of the books is {self.frm} (stock rows, option "
                f"roots and rename chains checked){self.hint} — harmless; "
                f"delete the line only if {self.frm} will not return")

    def record(self) -> Dict[str, Any]:
        out = {"line": self.line, "rule": f"{self.frm} -> {self.to}",
               "reason": self.reason, "kind": "unused-rule",
               "certainty": "verify"}
        if self.hint:
            out["hint"] = self.hint.strip(" ()")
        return out


def unused_rules(root: Path) -> Tuple[List[UnusedRule], List[str]]:
    """(the ticker.map rename rules whose FROM no parsed source of the
    project reaches, the parsed sources that could not be read). When a
    source cannot be read the first list is empty: its symbols are
    unknown (module docstring)."""
    root = Path(root)
    tm_path = _PL.ticker_map_path(root)
    if not tm_path.is_file():
        return [], []
    from taxjson.bin.taxjson_ticker_map import _parse_map_file
    try:
        tmap = _parse_map_file(tm_path)[0]
    except Exception:                                   # noqa: BLE001
        return [], []
    rules: Dict[str, Tuple[str, str]] = {}
    # A line tobase.map gives the map (the interlisted master's pairs and
    # the .V / .TO spellings, lib/tobase_map) is never "unused": most
    # pairs are there for a security the books may hold one day.
    generated = set(getattr(tmap, "generated", ()) or ())
    for kw, table in (("GLOBAL", tmap.glob), ("TOBASE", tmap.tobase),
                      ("JOURNAL", tmap.journal)):
        for frm, to in table.items():
            if frm in generated:
                continue
            rules[str(frm)] = (kw, str(to))
    if not rules:
        return [], []
    from taxjson.bin.taxjson_run import _audit_source_files
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    cache = root / "work"
    accounts = _accounts(_config(root))
    seen: Set[str] = set()
    unread: List[str] = []
    for acct in accounts:
        for p in _audit_source_files(cache, acct, accounts):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError) as e:
                unread.append(f"{p.name} ({e})")
                continue
            rows = (doc.get("transactions", doc)
                    if isinstance(doc, dict) else doc) or []
            # A wrong-shape stage file ([1] / {"transactions": [1]}) is
            # unreadable too (re-audit A2-1440).
            if not isinstance(rows, list) or any(
                    not isinstance(t, dict) for t in rows):
                unread.append(f"{p.name} (not a list of transaction "
                              f"objects)")
                continue
            for t in rows:
                sym = str(t.get("symbol") or "").upper()
                if sym:
                    seen.add(sym)
    if unread:
        return [], unread
    reached: Set[str] = set()
    for sym in seen:
        reached.add(sym)
        if is_option_symbol(sym):
            try:
                und = parse_option_underlying(sym)
            except Exception:                           # noqa: BLE001
                und = None
            if und:
                reached.add(str(und).upper())
    rules_u = {k.upper(): v[1].upper() for k, v in rules.items()}
    frontier = list(reached)
    while frontier:
        nxt = rules_u.get(frontier.pop())
        if nxt and nxt not in reached:
            reached.add(nxt)
            frontier.append(nxt)
    suffixed: Dict[str, Set[str]] = {}
    for sym in reached:
        if "." in sym:
            suffixed.setdefault(sym.rsplit(".", 1)[0], set()).add(sym)
    out: List[UnusedRule] = []
    for frm, (kw, to) in sorted(rules.items()):
        fu = frm.upper()
        if fu in reached:
            continue
        hint = ""
        if "." not in fu and fu in suffixed:
            alts = sorted(suffixed[fu])
            hint = (f" (the books only have {', '.join(alts)}; a rule's "
                    f"FROM matches exactly — write the suffixed form, "
                    f"e.g. {alts[0]})")
        out.append(UnusedRule(kw, frm, to, hint))
    return out, []


def first_unread(unread: List[str]) -> Optional[str]:
    """The sentence a listing says when the unused-rule check was
    skipped (None when it ran)."""
    if not unread:
        return None
    return (f"The unused-rule check is skipped: could not read "
            f"{'; '.join(unread)} — re-run `taxjson run`.")

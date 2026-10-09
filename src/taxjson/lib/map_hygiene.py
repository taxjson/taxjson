"""ticker.map hygiene read from the last run's files: the rules no symbol
of the books reaches (`taxjson ticker-map --suggest`, "unused rule,
delete?"), and the listing helpers `taxjson tips` and the map checks
share (a symbol's root and listing suffix, the security names the
exports give each listing, and what those names say about a US and a
Canadian listing of one root).

A data check, not a tax rule (both countries). Nothing here writes.

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
from typing import Any, Dict, List, Optional, Set, Tuple


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
                 shown: Dict[tuple, str]) -> Tuple[str, str]:
    """What the exports say about a US and a Canadian listing that share
    a root: (kind, text). A shared root is a candidate (many interlisted
    shares keep their letters) unless something shows the two apart:
    "receipt" — the Canadian line is a depositary receipt (a receipt
    word of markets.toml [lists] receipt_words in its name, or a listing
    on a receipt venue); "different" — the names name different
    companies (no leading company word in common): cross_listings.
    shown_apart. Otherwise "same" (equal names under the cross-listing
    join's rule, _names_verdict over symbol_codes.exact_name; text: the
    name as written), "unequal" (the names are not equal word for word;
    text: why) or "unknown" (no name for a side; text: which)."""
    from taxjson.lib import cross_listings as XL
    nu, nc = names.get(us, set()), names.get(ca, set())
    apart = XL.shown_apart(us, ca, names)
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


def _config(root: Path) -> Dict[str, Any]:
    from taxjson.lib.tomlcompat import tomllib
    try:
        cfg = tomllib.loads((Path(root) / "taxjson.toml").read_text(
            encoding="utf-8-sig")) if tomllib is not None else {}
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
    tm_path = root / "ticker.map"
    if not tm_path.is_file():
        return [], []
    from taxjson.bin.taxjson_ticker_map import _parse_map_file
    try:
        tmap = _parse_map_file(tm_path)[0]
    except Exception:                                   # noqa: BLE001
        return [], []
    rules: Dict[str, Tuple[str, str]] = {}
    for kw, table in (("GLOBAL", tmap.glob), ("TOBASE", tmap.tobase),
                      ("JOURNAL", tmap.journal)):
        for frm, to in table.items():
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

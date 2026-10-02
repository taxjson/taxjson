#!/usr/bin/env python3
"""
taxjson_lint_crosslistings.py

Lint for cross-listed securities the wash radar would treat as two separate
holdings. The radar keys positions by exchange-suffixed symbol and relies on
ticker.map `TOBASE` consolidation (applied upstream) to merge a true
interlisting (e.g. AEM.US → AEM.TO). This lint scans the radar's OWN input
(the taxable/sheltered transaction files) for any root that still appears on
BOTH `.TO` and `.US`, and classifies each:

  OK    — CDR (CIBC depositary receipt: a DIFFERENT instrument, correctly
          separate), a JOURNAL/Norbert's-Gambit pair, or a pair ticker.map
          declares `DISTINCT` (two different securities).

A listing counts when the books hold its shares OR options on it (an
option on a security is substituted property for the superficial-loss
rule, s.54), and share positions follow SPLIT ratios and renames.
  WARN  — a TOBASE entry links the pair but BOTH listings are still present,
          so consolidation did not actually apply (e.g. the .TO leg isn't
          held in that account). The radar will MISS the superficial-loss
          link between them.
  REVIEW— same ticker on both exchanges with no rule. Either a genuine
          interlisting that needs a TOBASE entry, OR two different companies
          that share a ticker (e.g. CMG.TO Computer Modelling vs CMG.US
          Chipotle; EFX.TO Enerflex vs EFX.US Equifax) — a human must decide.

WARN/REVIEW rows carrying TAXABLE exposure are the actionable ones (a sheltered
loss isn't a superficial-loss trigger on its own).

Usage:
    taxjson-lint-crosslistings --taxable t.json [...] --sheltered s.json [...] \\
        [--map ticker.map] [--strict]
"""

import argparse
import re
import sys

from taxjson.lib.cli_diag import guard_main
from typing import Any, Dict, List

OPT_RE = re.compile(r"\d{6}[CP]\d{6,}")  # OCC option symbol


def _is_option(sym: str) -> bool:
    return bool(OPT_RE.search(sym or ""))


def _load_txs(paths: List[str]) -> List[Dict[str, Any]]:
    """Every row of every file, each tagged `_src` (its file's index):
    a SPLIT scales only the position of the book it is in."""
    out: List[Dict[str, Any]] = []
    from taxjson.lib.json_input import load_json_doc_or_exit, rows_or_exit
    for i, p in enumerate(paths):
        # An unreadable book stops the lint: skipping it printed
        # '(Clean.)' at exit 0, even under --strict (audit S035-13,
        # S028-09); a bare-array book is accepted (S079-11).
        doc = load_json_doc_or_exit("taxjson-lint-crosslistings", p)
        rows = rows_or_exit("taxjson-lint-crosslistings", doc, p,
                            "transactions")
        # The canonical row funnel, as wash-radar uses: a non-numeric
        # quantity is one line naming the file and row, not a
        # traceback (audit S053-01).
        from taxjson.lib.core import coerce_transaction_row
        for n, t in enumerate(rows):
            try:
                coerce_transaction_row(
                    {k: v for k, v in t.items()
                     if not str(k).startswith('_')}, n, str(p))
            except ValueError as e:
                print(f"taxjson-lint-crosslistings: error: {e}",
                      file=sys.stderr)
                sys.exit(2)
            out.append(dict(t, _src=i) if isinstance(t, dict) else t)
    return out


def _load_map(path):
    """(tobase, journal, distinct) as sets of symbol pairs, read by the
    engine's own ticker.map parser: the lint's private reader skipped
    lower-case keywords and every DISTINCT line, so a pair the map
    settles was still a REVIEW (R1-144). A map that cannot be read
    stops the lint instead of reading as 'no rules'."""
    tobase, journal, distinct = set(), set(), set()
    if not path:
        return tobase, journal, distinct
    from pathlib import Path
    from taxjson.bin.taxjson_ticker_map import load_map_file
    try:
        tmap = load_map_file(Path(path))
    except (OSError, UnicodeDecodeError) as e:
        # Exit 2 (an unreadable input), not 1 (a lint finding), so a
        # caller can tell the two apart (re-audit A2-1421 / A2-1435).
        print(f"taxjson-lint-crosslistings: error: cannot read map "
              f"{path}: {e}", file=sys.stderr)
        sys.exit(2)
    tobase = {frozenset((a, b)) for a, b in tmap.tobase.items()}
    journal = {frozenset((a, b)) for a, b in tmap.journal.items()}
    distinct = {frozenset(p) for p in tmap.distinct}
    return tobase, journal, distinct


_LISTED = (".TO", ".US")


def _net_by_symbol(txs):
    """(net shares by listing, net option contracts by the underlying
    listing, descriptions). Shares follow each book's rows in order:
    BUYSELL/ASSIGN/TRANSFER/OPENING_BALANCE add, a SPLIT scales by its
    ratio (and moves the position to `symbol_new`) — summing quantities
    alone left a flat split position at -100 (S035-02). Option rows
    count toward their underlying's listing (S035-03)."""
    from taxjson.lib.core import parse_option_underlying
    by_src: Dict[Any, Dict[str, float]] = {}
    opt: Dict[str, float] = {}
    desc: Dict[str, str] = {}
    for t in txs:
        if not isinstance(t, dict):
            continue
        s = t.get("symbol", "") or ""
        act = t.get("action")
        if _is_option(s):
            und = parse_option_underlying(s)
            if und and und.endswith(_LISTED) and act in ("BUYSELL", "ASSIGN"):
                opt[und] = opt.get(und, 0.0) + float(t.get("quantity") or 0)
            continue
        if s.endswith(_LISTED):
            # The broker's security name when the description is only
            # the ticker (IB rows: audit S057-24).
            name = t.get("security_name") or t.get("description")
            if name and s not in desc:
                desc[s] = name
        net = by_src.setdefault(t.get("_src"), {})
        if act in ("BUYSELL", "ASSIGN", "TRANSFER", "OPENING_BALANCE"):
            net[s] = net.get(s, 0.0) + float(t.get("quantity") or 0)
        elif act == "SPLIT":
            ratio = float(t.get("quantity") or 0)
            dst = (t.get("symbol_new") or "").strip() or s
            moved = net.pop(s, 0.0) * ratio
            net[dst] = (net.get(dst, 0.0) if dst != s else 0.0) + moved
    total: Dict[str, float] = {}
    for net in by_src.values():
        for s, q in net.items():
            if s.endswith(_LISTED):
                total[s] = total.get(s, 0.0) + q
    return total, opt, desc


def analyze(taxable_txs, sheltered_txs, tobase, journal, distinct=()):
    tax_net, tax_opt, tax_desc = _net_by_symbol(taxable_txs)
    shl_net, shl_opt, shl_desc = _net_by_symbol(sheltered_txs)
    desc = {**shl_desc, **tax_desc}
    roots: Dict[str, set] = {}
    for s in set(tax_net) | set(shl_net) | set(tax_opt) | set(shl_opt):
        root, ex = s.rsplit(".", 1)
        roots.setdefault(root, set()).add(ex)

    findings = []
    for root in sorted(r for r, ex in roots.items() if {"TO", "US"} <= ex):
        to, us = root + ".TO", root + ".US"
        blob = (desc.get(to, "") + " " + desc.get(us, "")).upper()
        pair = frozenset((to, us))
        if "CDR" in blob or "DEPOSITARY" in blob:
            sev, note = "OK", "CDR — different instrument, correctly separate"
        elif pair in journal:
            sev, note = "OK", "JOURNAL / Norbert's Gambit pair"
        elif pair in distinct:
            sev, note = ("OK", "DISTINCT in ticker.map — declared separate "
                         "securities")
        elif pair in tobase:
            sev, note = ("WARN",
                         "TOBASE entry exists but BOTH listings still present "
                         "— consolidation did NOT apply; radar treats them "
                         "separately")
        else:
            sev, note = ("REVIEW",
                         "interlisted (add a TOBASE entry) OR two different "
                         "companies sharing a ticker (leave separate)")
        findings.append({
            "root": root, "severity": sev, "note": note,
            "tax_to": tax_net.get(to, 0.0), "tax_us": tax_net.get(us, 0.0),
            "shl_to": shl_net.get(to, 0.0), "shl_us": shl_net.get(us, 0.0),
            "tax_to_opt": tax_opt.get(to, 0.0),
            "tax_us_opt": tax_opt.get(us, 0.0),
            "shl_to_opt": shl_opt.get(to, 0.0),
            "shl_us_opt": shl_opt.get(us, 0.0),
            "options_only": [x for x in ("TO", "US")
                             if f"{root}.{x}" not in tax_net
                             and f"{root}.{x}" not in shl_net],
            "desc_to": desc.get(to, ""), "desc_us": desc.get(us, ""),
        })
    return findings


from taxjson.lib.income_dating import CA_LISTING_SUFFIXES as _CA_VENUES


def venue_splits(taxable_txs, sheltered_txs):
    """Roots held under two CANADIAN venue suffixes (ABC.TO and ABC.V /
    .CN / .NE). The broker parsers spell every Canadian listing ROOT.TO
    (base.canonical_ca_listing); a .V/.CN/.NE row comes from a .tt file
    or a ticker.map rule, and it splits one security into two ACB pools
    that the superficial-loss check never links (audit S010-05).
    Returns [{root, symbols, taxable}] sorted by root."""
    seen: Dict[str, Dict[str, bool]] = {}
    for txs, taxable in ((taxable_txs, True), (sheltered_txs, False)):
        for t in txs:
            s = t.get("symbol", "") or ""
            if _is_option(s) or "." not in s:
                continue
            root, ex = s.rsplit(".", 1)
            if ex.upper() not in _CA_VENUES:
                continue
            # FTN.PRA.TO and FTN.PR.A.TO are one preferred series, and
            # .VN is a Canadian venue too (audit A2-0300).
            from taxjson.lib.brokerages.base import canonical_ca_root
            root = canonical_ca_root(root)
            d = seen.setdefault(root, {})
            d[s] = d.get(s, False) or taxable
    out = []
    for root in sorted(seen):
        if len(seen[root]) > 1:
            out.append({"root": root, "symbols": sorted(seen[root]),
                        "taxable": any(seen[root].values())})
    return out


@guard_main("taxjson-lint-crosslistings")
def main():
    p = argparse.ArgumentParser(
        description="Flag cross-listed securities the wash radar may not "
                    "consolidate.")
    # nargs='+' + extend: both `--taxable a b` (historical) and repeated
    # `--taxable a --taxable b` (A2 composability) work.
    p.add_argument("--taxable", nargs="+", action="extend", default=[],
                   required=True, metavar="FILE",
                   help="Taxable transaction JSON file(s) (same as "
                        "wash-radar; repeatable).")
    p.add_argument("--sheltered", nargs="+", action="extend", default=[],
                   metavar="FILE",
                   help="Sheltered transaction JSON file(s) (repeatable).")
    p.add_argument("--map", dest="map_file", metavar="FILE", default=None,
                   help="ticker.map, to recognize TOBASE/JOURNAL coverage.")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero if any WARN/REVIEW with taxable "
                        "exposure is found (for CI / pre-commit use).")
    args = p.parse_args()

    tobase, journal, distinct = _load_map(args.map_file)
    taxable_txs = _load_txs(args.taxable)
    sheltered_txs = _load_txs(args.sheltered)
    findings = analyze(taxable_txs, sheltered_txs, tobase, journal, distinct)
    splits = venue_splits(taxable_txs, sheltered_txs)

    actionable = 0
    if splits:
        print("CANADIAN VENUE SPLIT — one root under two Canadian suffixes")
        print("(taxjson books every Canadian listing as ROOT.TO; add "
              "`GLOBAL ROOT.V ROOT.TO` to ticker.map, or fix the .tt line)")
        for v in splits:
            flag = "WARN ‼" if v["taxable"] else "WARN"
            if v["taxable"]:
                actionable += 1
            print(f"  [{flag}] {v['root']}: {', '.join(v['symbols'])}")
        print()

    print("CROSS-LISTING LINT — roots present on both .TO and .US")
    print()
    if not findings:
        print("No cross-listed roots found in the input. (Clean.)")
        if args.strict and actionable:
            print(f"\nstrict: {actionable} Canadian venue split(s) with "
                  f"taxable exposure.", file=sys.stderr)
            return 1
        return 0

    order = {"WARN": 0, "REVIEW": 1, "OK": 2}
    for f in sorted(findings, key=lambda x: (order[x["severity"]], x["root"])):
        tax_exposed = any(abs(f[k]) > 1e-6 for k in (
            "tax_to", "tax_us", "tax_to_opt", "tax_us_opt"))
        flag = f["severity"]
        if flag in ("WARN", "REVIEW") and tax_exposed:
            flag += " ‼"           # taxable exposure → actionable now
            actionable += 1
        print(f"\n[{flag}] {f['root']}  ({f['note']})")
        for x in ("to", "us"):
            o_t, o_s = f[f"tax_{x}_opt"], f[f"shl_{x}_opt"]
            opts = (f"  options: taxable={o_t:+.0f} sheltered={o_s:+.0f}"
                    if (o_t or o_s or x.upper() in f["options_only"]) else "")
            print(f"    .{x.upper()}  taxable={f[f'tax_{x}']:+.0f}  "
                  f"sheltered={f[f'shl_{x}']:+.0f}{opts}"
                  f"   {f[f'desc_{x}'][:48]!r}")

    print("\n" + "-" * 100)
    print("OK = correctly separate (CDR / Norbert's / DISTINCT). "
          "WARN = mapped but not consolidated. "
          "REVIEW = needs a human call. ‼ = has taxable exposure (act now).")
    if args.strict and actionable:
        print(f"\nstrict: {actionable} actionable cross-listing(s) with taxable "
              f"exposure.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

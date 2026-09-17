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
          separate), or a JOURNAL/Norbert's-Gambit pair.
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
import json
import re
import sys

from taxjson.lib.report_model import load_report_json
from typing import Any, Dict, List

OPT_RE = re.compile(r"\d{6}[CP]\d{6,}")  # OCC option symbol


def _is_option(sym: str) -> bool:
    return bool(OPT_RE.search(sym or ""))


def _load_txs(paths: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in paths:
        try:
            out.extend(load_report_json(p).get("transactions", []))
        except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
            print(f"warning: skipping {p}: {e}", file=sys.stderr)
    return out


def _load_map(path):
    tobase, journal = set(), set()
    if not path:
        return tobase, journal
    try:
        for ln in open(path, encoding="utf-8"):
            s = ln.split("#")[0].split()
            if len(s) >= 3 and s[0] == "TOBASE":
                tobase.add(frozenset((s[1], s[2])))
            elif len(s) >= 3 and s[0] == "JOURNAL":
                journal.add(frozenset((s[1], s[2])))
    except OSError as e:
        print(f"warning: could not read map {path}: {e}", file=sys.stderr)
    return tobase, journal


def _net_by_symbol(txs):
    """{(symbol): net_qty} over BUYSELL/ASSIGN equity rows, + descriptions."""
    net: Dict[str, float] = {}
    desc: Dict[str, str] = {}
    for t in txs:
        s = t.get("symbol", "")
        if not (s.endswith(".TO") or s.endswith(".US")) or _is_option(s):
            continue
        if t.get("description") and s not in desc:
            desc[s] = t["description"]
        if t.get("action") in ("BUYSELL", "ASSIGN"):
            net[s] = net.get(s, 0.0) + float(t.get("quantity") or 0)
    return net, desc


def analyze(taxable_txs, sheltered_txs, tobase, journal):
    tax_net, tax_desc = _net_by_symbol(taxable_txs)
    shl_net, shl_desc = _net_by_symbol(sheltered_txs)
    desc = {**shl_desc, **tax_desc}
    roots: Dict[str, set] = {}
    for s in set(tax_net) | set(shl_net):
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
            "desc_to": desc.get(to, ""), "desc_us": desc.get(us, ""),
        })
    return findings


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

    tobase, journal = _load_map(args.map_file)
    findings = analyze(_load_txs(args.taxable), _load_txs(args.sheltered),
                       tobase, journal)

    print("CROSS-LISTING LINT — roots present on both .TO and .US")
    print()
    if not findings:
        print("No cross-listed roots found in the input. (Clean.)")
        return 0

    actionable = 0
    order = {"WARN": 0, "REVIEW": 1, "OK": 2}
    for f in sorted(findings, key=lambda x: (order[x["severity"]], x["root"])):
        tax_exposed = abs(f["tax_to"]) > 1e-6 or abs(f["tax_us"]) > 1e-6
        flag = f["severity"]
        if flag in ("WARN", "REVIEW") and tax_exposed:
            flag += " ‼"           # taxable exposure → actionable now
            actionable += 1
        print(f"\n[{flag}] {f['root']}  ({f['note']})")
        print(f"    .TO  taxable={f['tax_to']:+.0f}  sheltered={f['shl_to']:+.0f}"
              f"   {f['desc_to'][:48]!r}")
        print(f"    .US  taxable={f['tax_us']:+.0f}  sheltered={f['shl_us']:+.0f}"
              f"   {f['desc_us'][:48]!r}")

    print("\n" + "-" * 100)
    print("OK = correctly separate (CDR / Norbert's). "
          "WARN = mapped but not consolidated. "
          "REVIEW = needs a human call. ‼ = has taxable exposure (act now).")
    if args.strict and actionable:
        print(f"\nstrict: {actionable} actionable cross-listing(s) with taxable "
              f"exposure.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

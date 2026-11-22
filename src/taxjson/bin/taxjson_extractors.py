#!/usr/bin/env python3
"""List every parser taxjson ships (taxjson.parsers.registry).

Diagnostic tool — shows each parser's id, the other spellings the
brokerage tool accepts, and the class that handles it.
"""

import argparse

from taxjson.lib import out as _out
from taxjson.parsers.registry import SPECS, TIERS


def main(argv=None):
    # argv was never parsed: --help and a typo'd flag both printed the
    # registry with rc 0 (audit R1-265).
    argparse.ArgumentParser(
        prog="taxjson-extractors",
        description="List the registered brokerage parser ids and the "
                    "parser class that handles each.").parse_args(argv)
    # One row per parser (taxjson.parsers.registry): its id and the
    # other spellings the brokerage tool accepts, its class as the
    # registry names it (nothing is imported: the listing shows what
    # is registered even when a parser cannot load), its tier.
    # A live exact Doc: each line written as it is added, as the print()
    # it replaces wrote it.
    d = _out.Doc(exact=True, width_=0, live=True)
    d.line(f"{'BROKERAGE IDS':<35} | {'PARSER CLASS':<22} | TIER")
    d.line('-' * 80)
    for s in sorted(SPECS, key=lambda s: s.cls):
        ids_str = ", ".join(sorted((s.id, *s.aliases)))
        d.line(f"{ids_str:<35} | {s.cls:<22} | {s.tier} {TIERS[s.tier]}")


if __name__ == "__main__":
    main()

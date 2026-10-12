#!/usr/bin/env python3
"""List all registered brokerage parsers.

Diagnostic tool — shows which brokerage IDs are registered and which
parser class handles each one.
"""

import argparse

from taxjson.lib.core import _BROKERAGES
import taxjson.bin.taxjson_brokerage  # noqa: F401  -- triggers registry population


def main(argv=None):
    # argv was never parsed: --help and a typo'd flag both printed the
    # registry with rc 0 (audit R1-265).
    argparse.ArgumentParser(
        prog="taxjson-extractors",
        description="List the registered brokerage parser ids and the "
                    "parser class that handles each.").parse_args(argv)
    # Group IDs by their parser class so 'questrade' and 'qt' show on one row.
    by_class = {}
    for bid, cls in _BROKERAGES.items():
        by_class.setdefault(cls, []).append(bid)

    print(f"{'BROKERAGE IDS':<35} | PARSER CLASS")
    print('-' * 80)
    for cls, ids in sorted(by_class.items(), key=lambda kv: kv[0].__name__):
        ids_str = ", ".join(sorted(ids))
        print(f"{ids_str:<35} | {cls.__name__}")


if __name__ == "__main__":
    main()

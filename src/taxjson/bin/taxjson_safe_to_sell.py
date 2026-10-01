#!/usr/bin/env python3
"""
taxjson_safe_to_sell.py

Proactive superficial loss prevention: one line per TAXABLE position —
may it be sold at a loss today?

A thin view over the wash radar's walk (taxjson-wash-radar). This used
to be a separate port of tt_safe_to_sell.pl with its own position walk,
and every engine rule the radar learned since was missing here: a buy
made today (settling tomorrow) was invisible, a short cover opened a
phantom long lot while real short positions vanished, and a ticker
rename left the old symbol "held" and SAFE (2026-09 audits R1-233,
S007-09, S050-03). Quantities and verdicts now come from the radar, so
the two can never disagree.

Usage:
    python -m taxjson.bin.taxjson_safe_to_sell --taxable tax1.json --sheltered sh1.json [--date YYYY-MM-DD]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.country import add_country_argument

# Radar category -> this view's status. EXITABLE: only a FULL exit is
# clean (a partial loss sale is superficial); LOCKED: a registered
# account's in-window buy it still holds denies the loss (PARTIAL when
# only some units are at risk); VIOLATION: a loss already made is
# superficial unless the replacement is sold by the deadline.
_STATUS = {
    "CLEAR": "SAFE", "RISK": "SAFE", "BLOCKED": "SAFE", "COOLING": "SAFE",
    "CAUTION": "SAFE*", "EXITABLE": "FULL-EXIT-ONLY", "LOCKED": "LOCKED",
    "VIOLATION": "VIOLATION",
    # US §1091: an earlier loss is already disallowed (nothing to
    # rescue); selling the replacement realizes it.
    "WASHED": "SAFE*",
}


@guard_main("taxjson-safe-to-sell")
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Taxable-Only Safe-to-Sell Audit")
    # nargs='+' + extend: both `--taxable a b` (historical) and repeated
    # `--taxable a --taxable b` (A2 composability) work.
    parser.add_argument("--taxable", nargs='+', action='extend', default=[],
                        required=True, metavar="FILE",
                        help="Taxable transaction JSON files (repeatable)")
    parser.add_argument("--sheltered", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="Sheltered transaction JSON files (repeatable)")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--gains", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="The engine's gains files (passed to the radar: "
                             "the engine decides which sales were losses)")
    parser.add_argument("--incomplete-history", metavar="FILE", default=None,
                        help="phantoms.json (passed to the radar)")
    add_country_argument(parser, help="Project country (required; passed "
                                      "to the radar)")
    args = parser.parse_args(argv)

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--date {args.date!r} is not a valid YYYY-MM-DD date")

    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
           "--taxable", *args.taxable, "--all", "--json",
           "--country", args.country]
    if args.sheltered:
        cmd += ["--sheltered", *args.sheltered]
    if args.date:
        cmd += ["--date", args.date]
    if args.gains:
        cmd += ["--gains", *args.gains]
    if args.incomplete_history:
        cmd += ["--incomplete-history", args.incomplete_history]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        return res.returncode or 1
    doc = json.loads(res.stdout)

    usa = args.country == "usa"
    print("TAXABLE-ONLY SAFE-TO-SELL AUDIT — "
          + ("§1091 30-day window (trade dates)" if usa
             else "CRA 30-day window"))
    print()
    headers = ["TICKER", "TAXABLE QTY", "STATUS", "REASON"]
    output_rows = []
    for sec in doc.get("sections") or []:
        for r in sec.get("rows") or []:
            q = float(r.get("taxable_qty") or 0.0)
            if abs(q) <= 1e-6:
                continue
            cat = r.get("category") or ""
            status = _STATUS.get(cat, cat or "-")
            if cat == "LOCKED" and r.get("at_risk_qty") is not None \
                    and float(r["at_risk_qty"]) < abs(q) - 1e-6:
                status = "PARTIAL"
            adv = r.get("advisory") or ""
            reason = adv.split(":", 1)[1].strip() if ":" in adv else adv
            output_rows.append([r.get("ticker"), f"{q:.4f}", status, reason])
    output_rows.sort(key=lambda row: row[0])

    if not output_rows:
        print("No taxable holdings found.")
        return 0

    widths = [len(h) for h in headers]
    for row in output_rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("-" * (sum(widths) + 2 * (len(widths)-1)))

    for row in output_rows:
        print(fmt.format(*row))

    _sl = "a wash sale" if usa else "superficial"
    print("\nNOTE: statuses are the wash radar's (`taxjson wash-radar` "
          "explains each). SAFE means no replacement bought in the last "
          f"30 days would make a loss sale {_sl}; FULL-EXIT-ONLY "
          "means only selling the whole position is clean.")
    print("To fully avoid "
          + ("a wash sale" if usa else "superficial loss")
          + ", you must also NOT REPURCHASE for 30 days AFTER selling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

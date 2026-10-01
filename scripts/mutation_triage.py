#!/usr/bin/env python3
"""Triage mutation survivors: separate observable-behavior candidates
from cosmetic/diagnostic noise (trace lines, prints, warnings, pure
formatting), and group by source line for review.

Usage: scripts/mutation_triage.py [REPORT]   (default: mutation_report.txt
at the repository root, as scripts/mutation_audit.py writes it). Read-only.
"""
import argparse
import os
import re
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT = os.path.join(REPO, "mutation_report.txt")

FILES = {"core.py": f"{REPO}/src/taxjson/lib/core.py",
         "pipeline.py": f"{REPO}/src/taxjson/lib/pipeline.py"}
NOISE = re.compile(
    r"trace|print|warning|note:|symbol_acb_traces|rg_trace|"
    r"win_txs|role|raw_days|window_start|days_from|"
    r"f\"|f'|_wrap|days_held|adjustment_shown|_shown_adj|"
    r"clear_in|summary|window_start|win_txs|description")
EPS = re.compile(r"1e-\d|0\.005|0\.001|epsilon")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Group mutation_audit.py survivors into CANDIDATE / "
                    "BOUNDARY-EQUIV / NOISE (read-only).")
    ap.add_argument("report", nargs="?", default=DEFAULT_REPORT,
                    help="mutation report to read (default: %(default)s)")
    args = ap.parse_args(argv)
    try:
        with open(args.report, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        # argv[1] was taken as the path unconditionally: --help was a
        # FileNotFoundError traceback (audit S025-04).
        print(f"mutation_triage: error: cannot read report {args.report}: "
              f"{e.strerror or e}", file=sys.stderr)
        return 1

    srcs = {}
    for k, v in FILES.items():
        with open(v) as f:
            srcs[k] = f.read().splitlines()
    survivors = []
    for line in lines:
        m = re.match(r"\s+(\S+):(\d+) \[(\w+)\]", line)
        if m:
            survivors.append((m.group(1), int(m.group(2)), m.group(3)))

    by_class = defaultdict(list)
    for f, ln, kind in survivors:
        src = srcs.get(f, [])
        text = src[ln - 1].strip() if 0 < ln <= len(src) else "?"
        if NOISE.search(text):
            cls = "NOISE"
        elif kind in ("cmp", "const") and EPS.search(text):
            # >-to->= (etc.) at a float epsilon boundary, or scaling the
            # epsilon itself: behaviorally equivalent unless a quantity
            # lands EXACTLY on the boundary, which share counts never do.
            cls = "BOUNDARY-EQUIV"
        else:
            cls = "CANDIDATE"
        by_class[cls].append((f, ln, kind, text))

    for cls in ("CANDIDATE", "BOUNDARY-EQUIV", "NOISE"):
        rows = by_class.get(cls, [])
        print(f"== {cls}: {len(rows)} ==")
        if cls == "CANDIDATE":
            for f, ln, kind, text in sorted(set(rows)):
                print(f"  {f}:{ln} [{kind}] {text[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

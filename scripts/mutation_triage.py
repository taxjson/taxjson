import os
#!/usr/bin/env python3
"""Triage mutation survivors: separate observable-behavior candidates
from cosmetic/diagnostic noise (trace lines, prints, warnings, pure
formatting), and group by source line for review."""
import re
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "mutation_report.txt")

FILES = {"core.py": f"{REPO}/src/taxjson/lib/core.py",
         "pipeline.py": f"{REPO}/src/taxjson/lib/pipeline.py"}
NOISE = re.compile(
    r"trace|print|warning|note:|symbol_acb_traces|rg_trace|"
    r"win_txs|role|raw_days|window_start|days_from|"
    r"f\"|f'|_wrap|days_held|adjustment_shown|_shown_adj|"
    r"clear_in|summary|window_start|win_txs|description")

srcs = {k: open(v).read().splitlines() for k, v in FILES.items()}
survivors = []
for line in open(REPORT):
    m = re.match(r"\s+(\S+):(\d+) \[(\w+)\]", line)
    if m:
        survivors.append((m.group(1), int(m.group(2)), m.group(3)))

EPS = re.compile(r"1e-\d|0\.005|0\.001|epsilon")
by_class = defaultdict(list)
for f, ln, kind in survivors:
    text = srcs[f][ln - 1].strip() if ln <= len(srcs[f]) else "?"
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

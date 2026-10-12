#!/usr/bin/env python3
"""Measure survey captures (scripts/style/survey.py OUTDIR): per capture
the longest line, lines over 100 / 80 columns (prose vs table rows), the
longest prose blob, headings, blank-line runs and the prefixes used; then
one line per command, worst first. Writes OUTDIR/metrics.json.

    python3 scripts/style/measure.py OUTDIR
    python3 scripts/style/measure.py --inventory OUTDIR [BEFORE_DIR]
"""
import json, re, sys
from pathlib import Path

if len(sys.argv) > 2 and sys.argv[1] == "--inventory":
    # The concise-output inventory (docs/output-style.md, Essentials
    # first): per capture the lines the budget counts (lib/out.classify
    # "prose": not a table row, heading, step or figure line) and their
    # words, worst first. `--inventory OUTDIR [BEFORE_DIR]`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from taxjson.lib import out as _o

    def _inv(d):
        res = {}
        for f in sorted(Path(d).glob("*/*.txt")):
            body = "\n".join(f.read_text(errors="replace").split("\n")[2:])
            pl = _o.prose_lines(body)
            res[f"{f.parent.name}/{f.stem}"] = (
                len(pl), sum(len(x.split()) for x in pl),
                len(_o.act_lines(body)),
                sum(1 for k, _ in _o.classify(body) if k == "step"))
        return res
    now = _inv(sys.argv[2])
    old = _inv(sys.argv[3]) if len(sys.argv) > 3 else {}
    print(f"{'capture':34} {'prose':>5} {'words':>6} {'!':>3} {'steps':>5}"
          + ("   before: prose  words" if old else ""))
    for k, (n, w, a, st) in sorted(now.items(), key=lambda x: -x[1][0]):
        b = old.get(k)
        print(f"{k:34} {n:5} {w:6} {a:3} {st:5}"
              + (f"   {b[0]:13} {b[1]:6}" if b else ""))
    json.dump(now, open(Path(sys.argv[2]) / "inventory.json", "w"),
              indent=1)
    sys.exit(0)

CAP = Path(sys.argv[1])

PREFIXES = [
    ("note:", r"(^|\s|: )note:"), ("NOTE:", r"\bNOTE:"), ("Note:", r"\bNote:"),
    ("warning:", r"(^|\s|: )warning:"), ("WARNING:", r"\bWARNING\b:?"),
    ("Warning:", r"\bWarning:"), ("WARN", r"\bWARN\b"),
    ("ATTENTION", r"\bATTENTION\b"), ("error:", r"(^|\s|: )error:"),
    ("ERROR", r"\bERROR\b"), ("!!", r"(^|\s)!!"), ("[!]", r"\[!\]"),
    ("->/→ bullet", r"^\s*(→|->)\s"), ("* bullet", r"^\s*\*\s"),
    ("tip:", r"(?i)\btip:"), ("hint:", r"(?i)\bhint:"),
]

def is_table(line):
    s = line.rstrip()
    t = s.strip()
    if not t:
        return False
    if re.fullmatch(r"[-=─━_+|\s]{3,}", t):
        return True
    # two or more column gaps (2+ spaces between non-space tokens)
    gaps = re.findall(r"\S {2,}(?=\S)", t)
    if len(gaps) >= 2:
        return True
    # mostly numbers
    toks = t.split()
    nums = sum(bool(re.fullmatch(r"[-+(]?[\d,.$%]+\)?[A-Z]{0,3}\*?", x)) for x in toks)
    if len(toks) >= 3 and nums / len(toks) >= 0.5:
        return True
    return False

def is_heading(line):
    t = line.strip()
    if not t or len(t) > 100:
        return False
    if t.startswith("==") or t.startswith("##"):
        return True
    letters = re.sub(r"[^A-Za-z]", "", t.split(" — ")[0].split(" - ")[0])
    if len(letters) >= 3 and letters.isupper() and not is_table(line):
        return True
    if t.endswith(":") and len(t) < 60 and not t.startswith(("-", "*")):
        return True
    return False

def measure(path):
    raw = path.read_text(errors="replace").splitlines()
    lines = raw[2:]  # drop "$ tjs" and "# exit"
    exitc = raw[1].split()[-1] if len(raw) > 1 else "?"
    lens = [len(l.rstrip()) for l in lines]
    m = {
        "lines": len(lines), "exit": exitc,
        "max": max(lens, default=0),
        "gt100": sum(x > 100 for x in lens),
        "gt80": sum(x > 80 for x in lens),
    }
    # prose blobs: consecutive non-blank, non-table lines
    best_c = best_l = cur_c = cur_l = 0
    for l in lines:
        if l.strip() and not is_table(l) and not is_heading(l):
            if re.match(r"\s*([-*•]|\d+[.)])\s", l):
                cur_c = cur_l = 0       # a list item starts a new blob
            cur_c += len(l.strip()); cur_l += 1
            if cur_c > best_c:
                best_c, best_l = cur_c, cur_l
        else:
            cur_c = cur_l = 0
    m["blob_chars"], m["blob_lines"] = best_c, best_l
    m["long_prose"] = sum(1 for l in lines if len(l) > 100 and not is_table(l))
    m["long_table"] = sum(1 for l in lines if len(l) > 100 and is_table(l))
    m["headings"] = sum(is_heading(l) for l in lines)
    runs = 0; prev_blank = False; bl = 0
    for l in lines:
        if not l.strip():
            bl += 1
        else:
            if bl >= 2: runs += 1
            bl = 0
    m["blank_runs"] = runs
    m["blank_lines"] = sum(1 for l in lines if not l.strip())
    used = {}
    for name, rx in PREFIXES:
        n = sum(1 for l in lines if re.search(rx, l))
        if n:
            used[name] = n
    m["prefixes"] = used
    # score: worst first
    m["score"] = round(
        3 * m["long_prose"] + 1 * m["long_table"] + 0.05 * (m["gt80"] - m["gt100"])
        + max(0, m["max"] - 100) / 20 + m["blob_chars"] / 300
        + 2 * max(0, len([k for k in used if k not in ("->/→ bullet", "* bullet")]) - 1)
        + 2 * m["blank_runs"]
        + (2 if m["lines"] > 15 and m["headings"] == 0 else 0), 1)
    return m

res = {}
for f in sorted(CAP.glob("*/*.txt")):
    res[f"{f.parent.name}/{f.stem}"] = measure(f)
json.dump(res, open(CAP / "metrics.json", "w"), indent=1)
# per-command (worst of the variants and countries)
cmd = {}
for k, m in res.items():
    c = k.split("/", 1)[1]
    base = c.split("_")[0]
    cmd.setdefault(base, []).append((k, m))
rows = []
for base, items in cmd.items():
    worst = max(items, key=lambda x: x[1]["score"])
    rows.append((sum(m["score"] for _, m in items) / len(items) + worst[1]["score"], base, worst))
rows.sort(reverse=True)
for s, base, (k, m) in rows:
    print(f"{s:7.1f} {base:22} {k:28} max={m['max']:4} >100={m['gt100']:3} >80={m['gt80']:3} "
          f"prose>100={m['long_prose']:3} blob={m['blob_chars']}c/{m['blob_lines']}l "
          f"heads={m['headings']} blankruns={m['blank_runs']} pref={','.join(m['prefixes'])}")

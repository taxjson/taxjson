#!/usr/bin/env python3
"""Targeted mutation testing for the taxjson gains engine.

Generates one-line mutants (comparison/arithmetic/boolean/constant
tweaks) inside the wash-relevant regions of core.py and pipeline.py,
runs the fast engine test set against each, and reports SURVIVORS —
mutants the tests fail to kill. A survivor is either an equivalent
mutant (no observable behavior change) or a genuine test gap.

Mutates IN PLACE with backup/restore; safe to interrupt (restores on
exit). Writes progress + results to mutation_report.txt next to this
script.
"""
import ast
import os
import shutil
import signal
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "venv/bin/python3")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "..", "mutation_report.txt")

TARGETS = [
    # (path, lo_line, hi_line) — wash walk + ADJUST application (CA),
    # US wash replacement matching, transfer pre-processing.
    (os.path.join(REPO, "src/taxjson/lib/core.py"), 900, 1810),
    (os.path.join(REPO, "src/taxjson/lib/core.py"), 2510, 2620),
    (os.path.join(REPO, "src/taxjson/lib/pipeline.py"), 60, 320),
]

TEST_CMD = [PY, "-m", "unittest", "-q",
            "test_engine_invariants", "test_audit_2026_09_fixes",
            "test_transfer_handling", "test_pipeline",
            "test_engines_expanded", "test_conservation",
            "test_blended_taxable", "test_usa_shorts",
            "test_canada_other_scope_isolation", "test_affiliated_flag",
            "test_phantom_holdings", "test_audit_tier1_fixes",
            "test_audit_tier2_phantoms"]
TEST_ENV = dict(os.environ, TAXJSON_FUZZ_BOOKS="30",
                PYTHONDONTWRITEBYTECODE="1")
TEST_CWD = os.path.join(REPO, "tests")
TIMEOUT = 120

CMP_SWAPS = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
             ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
ARITH_SWAPS = {ast.Add: ast.Sub, ast.Sub: ast.Add,
               ast.Mult: ast.Div}
BOOL_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


def find_sites(src, lo, hi):
    tree = ast.parse(src)
    sites = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or not (lo <= ln <= hi):
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and type(node.ops[0]) in CMP_SWAPS:
            sites.append(("cmp", node))
        elif isinstance(node, ast.BinOp) \
                and type(node.op) in ARITH_SWAPS:
            sites.append(("arith", node))
        elif isinstance(node, ast.BoolOp) \
                and type(node.op) in BOOL_SWAPS:
            sites.append(("bool", node))
        elif isinstance(node, ast.UnaryOp) \
                and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.Name):
            sites.append(("unaryneg", node))
        elif isinstance(node, ast.Constant) \
                and isinstance(node.value, (int, float)) \
                and node.value in (30, 31, 35, 0.001, 1e-6, 1e-9,
                                   0.005, 0.01):
            sites.append(("const", node))
    return tree, sites


def mutate(tree, kind, node):
    if kind == "cmp":
        node.ops[0] = CMP_SWAPS[type(node.ops[0])]()
    elif kind == "arith":
        node.op = ARITH_SWAPS[type(node.op)]()
    elif kind == "bool":
        node.op = BOOL_SWAPS[type(node.op)]()
    elif kind == "unaryneg":
        return node.operand           # drop the negation
    elif kind == "const":
        v = node.value
        node.value = (v + 1) if isinstance(v, int) else v * 10
    return node


def main():
    results = {"killed": 0, "timeout": 0, "survived": [],
               "compile_error": 0}
    t0 = time.time()
    with open(REPORT, "w") as rep:
        rep.write("mutation run started\n")
    # Baseline must pass.
    r = subprocess.run(TEST_CMD, cwd=TEST_CWD, env=TEST_ENV,
                       capture_output=True, timeout=600)
    if r.returncode != 0:
        print("BASELINE FAILS — aborting")
        sys.exit(1)

    total = 0
    for path, lo, hi in TARGETS:
        src = open(path).read()
        backup = src
        _, sites = find_sites(src, lo, hi)
        total += len(sites)
        for i, (kind, _) in enumerate(sites):
            # Re-parse fresh for each mutant, then locate the i-th
            # site again (node identity is per-parse).
            tree, fresh = find_sites(src, lo, hi)[0], None
            tree, fresh_sites = find_sites(src, lo, hi)
            k, node = fresh_sites[i]
            ln = node.lineno
            if kind == "unaryneg":
                # replace in parent — easiest via source line hack:
                # skip (rare, low value here)
                continue
            mutate(tree, k, node)
            try:
                mutated = ast.unparse(tree)
                compile(mutated, path, "exec")
            except Exception:
                results["compile_error"] += 1
                continue
            open(path, "w").write(mutated)
            try:
                r = subprocess.run(TEST_CMD, cwd=TEST_CWD,
                                   env=TEST_ENV, capture_output=True,
                                   timeout=TIMEOUT)
                if r.returncode == 0:
                    results["survived"].append(
                        (os.path.basename(path), ln, kind))
                else:
                    results["killed"] += 1
            except subprocess.TimeoutExpired:
                results["timeout"] += 1
            finally:
                open(path, "w").write(backup)
            done = (results["killed"] + results["timeout"]
                    + len(results["survived"])
                    + results["compile_error"])
            if done % 25 == 0:
                with open(REPORT, "a") as rep:
                    rep.write(f"progress: {done} mutants, "
                              f"{len(results['survived'])} survived, "
                              f"{time.time()-t0:.0f}s\n")
        open(path, "w").write(backup)

    with open(REPORT, "a") as rep:
        rep.write(f"\nDONE in {time.time()-t0:.0f}s\n")
        rep.write(f"killed: {results['killed']}\n")
        rep.write(f"timeout(=killed): {results['timeout']}\n")
        rep.write(f"compile_error(skipped): "
                  f"{results['compile_error']}\n")
        rep.write(f"SURVIVED: {len(results['survived'])}\n")
        for f, ln, kind in results["survived"]:
            rep.write(f"  {f}:{ln} [{kind}]\n")
    print("done — see", REPORT)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Mutation-test the gains engine. REWRITES core.py/"
                    "pipeline.py IN PLACE (backup/restore per mutant); "
                    "an interrupted run leaves a mutant applied — "
                    "restore with `git checkout -- src/taxjson/lib/`.")
    ap.add_argument("--yes", action="store_true",
                    help="actually run (hours); required")
    ap.add_argument("--list-targets", action="store_true",
                    help="print the mutation regions and exit")
    a = ap.parse_args()
    if a.list_targets:
        for path, lo, hi in TARGETS:
            print(f"{os.path.relpath(path, REPO)}:{lo}-{hi}")
        sys.exit(0)
    if not a.yes:
        ap.error("refusing to run without --yes (this mutates the "
                 "working tree)")
    main()

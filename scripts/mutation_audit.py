#!/usr/bin/env python3
"""Targeted mutation testing for the taxjson gains engine.

Generates one-line mutants (comparison/arithmetic/boolean/constant
tweaks) inside the wash-relevant regions of core.py and pipeline.py,
runs the fast engine test set against each, and reports SURVIVORS —
mutants the tests fail to kill. A survivor is either an equivalent
mutant (no observable behavior change) or a genuine test gap.

Mutates IN PLACE with backup/restore. The original source is written
back after every mutant, and on Ctrl-C, SIGTERM, SIGHUP (a killed
`timeout`, an ssh drop, a CI cancel) or any other exit Python gets to
run; only SIGKILL or a power cut can leave a mutant applied — restore
with `git checkout -- src/taxjson/lib/`. Writes progress + results to
mutation_report.txt next to this script.
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

# The regions to mutate, by FUNCTION NAME (audit S025-02: hand-entered
# line ranges went stale — the "US wash replacement" range had drifted
# into the Canadian engine, and the superficial-loss window and the
# whole US engine were never mutated). Resolved to line ranges with the
# AST at start-up; a name that no longer exists stops the harness.
TARGET_FUNCS = [
    # Canada: ACB pool walk, superficial-loss window/solver, ADJUST
    # application (all inside compute_gains).
    ("src/taxjson/lib/core.py", "CanadaTaxRules.compute_gains"),
    # US: FIFO lots and §1091 replacement matching.
    ("src/taxjson/lib/core.py", "USATaxRules.compute_gains"),
    # Transfer pre-processing.
    ("src/taxjson/lib/pipeline.py", "_drop_self_cancelling_transfers"),
    ("src/taxjson/lib/pipeline.py", "_net_cross_account_transfers"),
    ("src/taxjson/lib/pipeline.py", "_handle_transfers"),
]


def resolve_targets(funcs=TARGET_FUNCS):
    """[(abs path, first line, last line)] for each (path, qualname)."""
    out = []
    for rel, qual in funcs:
        path = os.path.join(REPO, rel)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        node, found = tree, None
        for part in qual.split("."):
            found = next((n for n in ast.iter_child_nodes(node)
                          if isinstance(n, (ast.FunctionDef,
                                            ast.AsyncFunctionDef,
                                            ast.ClassDef))
                          and n.name == part), None)
            if found is None:
                raise SystemExit(f"mutation_audit: {qual} not found in "
                                 f"{rel} — update TARGET_FUNCS")
            node = found
        out.append((path, found.lineno, found.end_lineno))
    return out


TARGETS = resolve_targets()

TEST_CMD = [PY, "-m", "unittest", "-q",
            "test_engine_invariants", "test_audit_2026_09_fixes",
            "test_transfer_handling", "test_pipeline",
            "test_engines_expanded", "test_conservation",
            "test_blended_taxable", "test_usa_shorts",
            "test_canada_other_scope_isolation", "test_affiliated_flag",
            "test_missing_history_lib", "test_audit_tier1_fixes",
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


# path -> original source of every file currently being mutated.
_ORIGINALS = {}


def _restore_originals():
    """Write every registered original back (idempotent)."""
    for path, src in list(_ORIGINALS.items()):
        try:
            with open(path, "w") as f:
                f.write(src)
        except OSError as e:
            print(f"mutation_audit: COULD NOT RESTORE {path}: {e} — run "
                  f"`git checkout -- {path}`", file=sys.stderr)
            continue
        _ORIGINALS.pop(path, None)


def _on_signal(signum, _frame):
    _restore_originals()
    print(f"mutation_audit: stopped by signal {signum}; sources restored",
          file=sys.stderr)
    os._exit(128 + signum)


def install_restore_handlers():
    """SIGTERM / SIGHUP used to kill the run between writing a mutant
    and the try/finally that restores it, leaving a de-commented,
    mutated engine behind (audit S025-00)."""
    import atexit
    atexit.register(_restore_originals)
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _on_signal)


def main():
    install_restore_handlers()
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
        _ORIGINALS[path] = backup
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
        _ORIGINALS.pop(path, None)

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

#!/usr/bin/env python3
"""Run the unittest suite across worker processes (standard library only).

    scripts/run_tests_parallel.py [--jobs N] [--serial] [MODULE...]

The serial suite (`python -m unittest discover -s tests`) was ~97% of
the gate's time, on one core. This runner runs the same tests on N:

  1. COLLECT: one process loads the suite as the serial run does
     (`TestLoader.discover`: the same start folder and pattern) and
     reports each module's test count and test classes. A module that
     fails to import is a failure here, as in the serial run, and so is
     a TestCase class with tests bound where it is not defined (in a
     second module, a helper's, or under a second name: unittest runs its
     tests there too).
  2. RUN: each module is a separate `python -m unittest _hermetic
     <module>` process; a module slow enough to hold up the whole run
     (over half a process's fair share of the expected time, and over
     SPLIT_OVER seconds) runs as one process per test class.
     N processes at a time, longest first: the expected time is the one
     the last run recorded (DURATIONS, a gitignored cache), else the
     file's size. Each process has its own TMPDIR and its own synthetic
     HOME (tests/_hermetic makes one per process), so two modules never
     share a temp file, a work folder or a rate cache.
  3. CHECK: every process exited 0, and the tests run add up to the
     tests collected, part by part — a module that ran fewer tests than
     it holds (a crash in setUpModule, an import that failed) fails; the
     total is asserted equal to the serial discovery's.

Output: a line per part as it finishes, then each failing part's full
output, the totals and the slowest 15 modules. Exit 0 only when all
passed. Nothing is retried: a test that fails only in parallel shares
something it must not (a fixed temp path, a port, a file in the
checkout) and is a bug to fix.

`--serial` runs the plain serial discovery instead (the fallback; the
command the gate ran before this runner).
"""
import argparse
import fnmatch
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DURATIONS = REPO / ".ci" / "test-durations.json"
# A module run alone gets `_hermetic` first, as discovery imports it
# first (it sorts first): the synthetic HOME is in place before a test
# module imports taxjson.
PRELOAD = "_hermetic"
# A module expected to take longer than this AND than half of one
# process's share of the whole run runs one class per process, so the
# run is not left waiting on one long module. (Splitting costs a
# process start and setUpModule per class: on the 2026-10 suite no
# module is that long at 16 processes, and none is split.)
SPLIT_OVER = 25.0
# Bytes of test source per second of run time, for a module with no
# recorded time (a fresh clone; measured 2026-10: ~4,800): only the
# order matters.
BYTES_PER_SECOND = 4800.0
SLOWEST = 15
# The environment a part must not inherit: the parent's synthetic HOME
# (each part makes its own) and a TMPDIR shared with other parts.
STRIP_ENV = ("TAXJSON_TEST_HOME",)

# The collecting process: the serial discovery's count, and each
# module's count and classes. JSON on stdout.
_COLLECT = r"""
import importlib, json, sys, unittest
start, pattern, preload = sys.argv[1], sys.argv[2], sys.argv[3]
names = json.loads(sys.stdin.read())
if preload:
    importlib.import_module(preload)
loader = unittest.TestLoader()
suite = loader.discover(start, pattern, top_level_dir=start)
total = suite.countTestCases()
errors = list(loader.errors)
mods, found = {}, {}
for name in names:
    try:
        mod = importlib.import_module(name)
    except Exception as e:  # discovery reported it too (loader.errors)
        errors.append(f"{name}: {type(e).__name__}: {e}")
        continue
    ld = unittest.TestLoader()
    msuite = ld.loadTestsFromModule(mod, pattern=pattern)
    count = msuite.countTestCases()
    errors.extend(ld.errors)
    # What the module runs, by class: its suite, as load_tests leaves it.
    # (unittest's own cases, a FunctionTestCase or a load failure's, are no
    # module's class; a doctest's is a helper's class, below.)
    ran, ids_of = {}, {}
    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            elif type(t).__module__.partition(".")[0] != "unittest":
                ran[type(t)] = ran.get(type(t), 0) + 1
                ids_of.setdefault(type(t), set()).add(t.id())
    walk(msuite)
    # Each name the module binds a class it runs under is a place (an
    # alias is a second, as the loader runs the class once per name); one
    # load_tests added from elsewhere is a place too.
    classes = {}
    for cls, k in ran.items():
        names_of = sorted(a for a, v in vars(mod).items() if v is cls)
        entry = found.setdefault(id(cls), (cls, [], {}))
        entry[1].extend((name, a) for a in names_of or ["(load_tests)"])
        entry[2][name] = ids_of[cls]
        if len(names_of) == 1:
            classes[names_of[0]] = k
    if hasattr(mod, "load_tests") or sum(classes.values()) != count:
        classes = {}
    mods[name] = {"count": count, "classes": classes}
# A class with tests runs wherever a module binds it: one found under two
# names (two modules, or an alias), or bound in a module that does not
# define it while its own module holds it at the top (a helper's), runs
# twice or from the wrong place. A class a factory builds is no module's
# top-level name: it runs where it is found, once. A helper's class that
# load_tests builds cases from (parameterised, or a doctest's) runs where
# they are put: it fails only when two modules give the same test id.
bound = []
collected = set(names)
for obj, places, ids in found.values():
    home = sys.modules.get(obj.__module__)
    at_home = getattr(home, "__dict__", {}).get(obj.__name__) is obj
    others = [f"{m}.{a}" for m, a in places if (m, a) != (obj.__module__, obj.__name__)]
    if not others:
        continue
    if obj.__module__ not in collected and all(a == "(load_tests)" for _m, a in places):
        sets = list(ids.values())
        if any(a & b for i, a in enumerate(sets) for b in sets[i + 1:]):
            bound.append(f"{', '.join(others)}: {obj.__module__}.{obj.__qualname__} "
                         f"(the same test ids)")
    elif len(places) > 1 or at_home:
        bound.append(f"{', '.join(others)}: {obj.__module__}.{obj.__qualname__}")
print(json.dumps({"total": total, "errors": errors, "modules": mods, "bound": sorted(bound)}))
"""

_RAN = re.compile(r"^Ran (\d+) tests? in ([0-9.]+)s$", re.MULTILINE)
_STATUS = re.compile(r"^(OK|FAILED|NO TESTS RAN)\b(.*)$", re.MULTILINE)

_live = set()            # running Popen objects (their process groups)
_live_lock = threading.Lock()
_stopping = threading.Event()


def discover_modules(start: Path, pattern: str):
    """The modules discovery visits, as dotted names, in its order: the
    matching files at the top of START and every package below it (a
    folder with an __init__.py — itself included, as discovery loads a
    package's own tests too)."""
    out = []

    def visit(folder: Path, prefix: str):
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix == ".py" and p.stem.isidentifier() \
                    and fnmatch.fnmatch(p.name, pattern):
                out.append(prefix + p.stem)
            elif p.is_dir() and p.name.isidentifier() \
                    and (p / "__init__.py").is_file():
                out.append(prefix + p.name)
                visit(p, prefix + p.name + ".")
    visit(start, "")
    return out


def child_env(tmp: Path) -> dict:
    """A part's environment: its own TMPDIR, an empty HOME of its own
    (tests/_hermetic replaces it with a synthetic one at import)."""
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    home = tmp / "home"
    (home / ".cache").mkdir(parents=True, exist_ok=True)
    env.update(TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp), HOME=str(home),
               XDG_CACHE_HOME=str(home / ".cache"),
               XDG_CONFIG_HOME=str(home / ".config"),
               PYTHONDONTWRITEBYTECODE=env.get("PYTHONDONTWRITEBYTECODE", ""),
               # What a test prints lands before unittest's summary, not after
               # it, so the last `Ran` line is the part's own (TJ-3729).
               PYTHONUNBUFFERED="1")
    if not env["PYTHONDONTWRITEBYTECODE"]:
        del env["PYTHONDONTWRITEBYTECODE"]
    return env


def run_proc(cmd, cwd, env, timeout, stdin_text=None, log=None):
    """Run CMD in a process group of its own (a test's own subprocesses
    are stopped with it on a timeout or ^C). Returns (rc, output,
    seconds); rc None = timed out."""
    t0 = time.monotonic()
    out = open(log, "w+b") if log else tempfile.TemporaryFile()
    with out:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out,
                             stderr=subprocess.STDOUT,
                             stdin=subprocess.PIPE if stdin_text is not None
                             else subprocess.DEVNULL,
                             start_new_session=True)
        with _live_lock:
            _live.add(p)
        try:
            if stdin_text is not None:
                p.stdin.write(stdin_text.encode())
                p.stdin.close()
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(p)
            p.wait()
            rc = None
        finally:
            with _live_lock:
                _live.discard(p)
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    return rc, text, time.monotonic() - t0


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_all():
    _stopping.set()
    with _live_lock:
        for p in list(_live):
            _kill(p)


def load_durations(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return {k: float(v) for k, v in d.get("parts", {}).items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def save_durations(path: Path, old: dict, new: dict):
    merged = dict(old)
    merged.update(new)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"parts": dict(sorted(merged.items()))},
                                  indent=0) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        print(f"   (durations not recorded: {e})", file=sys.stderr)


def select(collected: dict, names) -> tuple:
    """The collected modules the names ask for, and the names that are
    neither: a module (`test_x`, `rules.ca.sl.test_x`) runs whole; a class
    of one (`test_x.TestY`, as the no-extras stage names a split module's
    classes so the mover renames them with their class) runs alone, and a
    module whose every class is named runs whole. A class resolves only
    where the collect could count it (one name, no load_tests)."""
    chosen: dict = {}
    unknown = []
    for name in names:
        if name in collected:
            chosen[name] = None
            continue
        mod, _, cls = name.rpartition(".")
        if mod in collected and cls in collected[mod]["classes"]:
            if mod not in chosen:
                chosen[mod] = set()
            if chosen[mod] is not None:
                chosen[mod].add(cls)
            continue
        unknown.append(name)
    out = {}
    for mod, named in chosen.items():
        info = collected[mod]
        if named is None or named == set(info["classes"]):
            out[mod] = info
        else:
            classes = {c: info["classes"][c] for c in sorted(named)}
            out[mod] = {"count": sum(classes.values()), "classes": classes,
                        "only": True}
    return out, unknown


def plan(collected: dict, start: Path, durations: dict, split_over: float,
         jobs: int = 1):
    """The parts to run — (name, module, expected tests, expected
    seconds) — longest first. A module with two or more classes whose
    expected time exceeds both SPLIT_OVER and half of one process's
    share of the total runs a part per class."""
    parts = []
    guesses = {}
    for mod, info in collected.items():
        if not info["count"]:
            continue
        rel = Path(*mod.split("."))
        f = start / rel.with_suffix(".py")
        if not f.is_file():
            f = start / rel / "__init__.py"
        size = f.stat().st_size if f.is_file() else 0
        guesses[mod] = durations.get(mod, size / BYTES_PER_SECOND)
    limit = max(split_over, 0.5 * sum(guesses.values()) / max(1, jobs))
    for mod, guess in guesses.items():
        info = collected[mod]
        classes = info["classes"]
        if info.get("only") or (guess > limit and len(classes) > 1):
            total = sum(classes.values())
            for cls, n in classes.items():
                name = f"{mod}.{cls}"
                parts.append((name, mod, n,
                              durations.get(name, guess * n / total)))
        else:
            parts.append((mod, mod, info["count"], guess))
    parts.sort(key=lambda p: (-p[3], p[0]))
    return parts


def parse_result(text: str):
    """(tests run, status line) from a unittest run's output: the last
    `Ran` line and the first status line after it. A test may print its
    own of either; a part's stdout is unbuffered (child_env), so what it
    prints lands before the summary, and a status line after the real one
    is still a test's."""
    ran = list(_RAN.finditer(text))
    if not ran:
        st = _STATUS.findall(text)
        return None, (st[-1][0] + st[-1][1]).strip() if st else ""
    st = _STATUS.search(text, ran[-1].end())
    return int(ran[-1].group(1)), (st.group(1) + st.group(2)).strip() if st else ""


def skipped_of(text: str) -> int:
    """The tests a part skipped, from its status line (`OK (skipped=3)`,
    parse_result's): the suite's total is compared across a test move
    (PLAN.md step 9)."""
    m = re.search(r"\bskipped=(\d+)", parse_result(text)[1])
    return int(m.group(1)) if m else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the unittest suite across worker processes.")
    ap.add_argument("modules", nargs="*", metavar="MODULE",
                    help="only these test modules (default: the whole suite)")
    ap.add_argument("-j", "--jobs", type=int,
                    default=min(os.cpu_count() or 1, 16),
                    help="processes at a time (default: min(CPUs, 16))")
    ap.add_argument("--serial", action="store_true",
                    help="run the plain serial discovery instead")
    ap.add_argument("-s", "--start-dir", default=str(REPO / "tests"))
    ap.add_argument("-p", "--pattern", default="test_*.py")
    ap.add_argument("--durations", default=str(DURATIONS),
                    help="the recorded-times cache (default: .ci/test-durations.json)")
    ap.add_argument("--split-over", type=float, default=SPLIT_OVER,
                    help="split a module expected to take longer than "
                         "this many seconds, and than half a process's "
                         "share of the run, into a process per class "
                         f"(default {SPLIT_OVER:g}; 0 = never)")
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="seconds before one part is stopped and failed")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="a line for every part (default: failures and "
                         "a progress line every 10%%)")
    a = ap.parse_args(argv)
    py = sys.executable
    start = Path(a.start_dir).resolve()
    if a.serial:
        cmd = [py, "-m", "unittest", "discover", "-s", str(start),
               "-p", a.pattern, "-q"]
        print("serial:", " ".join(cmd[1:]), flush=True)
        return subprocess.call(cmd)
    if a.jobs < 1:
        ap.error("--jobs must be at least 1")
    preload = PRELOAD if (start / PRELOAD / "__init__.py").is_file() else ""
    t0 = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="taxjson-par-"))
    signal.signal(signal.SIGTERM, lambda *_: (kill_all(), sys.exit(143)))
    try:
        return _run(a, py, start, preload, root, t0)
    except KeyboardInterrupt:
        kill_all()
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        kill_all()
        shutil.rmtree(root, ignore_errors=True)


def _run(a, py, start, preload, root, t0) -> int:
    names = discover_modules(start, a.pattern)
    ctmp = root / "collect"
    ctmp.mkdir()
    rc, text, _ = run_proc([py, "-c", _COLLECT, str(start), a.pattern, preload],
                           start, child_env(ctmp), a.timeout,
                           stdin_text=json.dumps(names))
    try:
        col = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        col = None
    if rc != 0 or col is None:
        print(f"COLLECT FAILED (exit {rc}):\n{text}")
        return 1
    if col["errors"]:
        print("COLLECT FAILED — modules that do not load:")
        for e in col["errors"]:
            print(f"  {e}")
        return 1
    if col.get("bound"):
        # A TestCase class with tests runs wherever a module's suite holds
        # it: one two modules' suites hold, or one module binds under two
        # names, runs twice; a helper's runs from a module that does not
        # define it. (One suite that adds a class twice is its own choice.)
        bound = col["bound"]
        print(f"COLLECT FAILED — {len(bound)} TestCase class(es) with tests bound where they "
              f"are not defined (a second module or name: their tests run there too):")
        for b in bound[:20]:
            print(f"  {b}")
        if len(bound) > 20:
            print(f"  ... and {len(bound) - 20} more")
        return 1
    mods = col["modules"]
    whole = sum(m["count"] for m in mods.values())
    if whole != col["total"]:
        print(f"COLLECT FAILED: the modules found hold {whole} tests, "
              f"discovery {col['total']} — the module list misses some")
        return 1
    if a.modules:
        mods, unknown = select(mods, a.modules)
        if unknown:
            print("not test modules or classes here: " + ", ".join(unknown))
            return 2
    expected = sum(m["count"] for m in mods.values())
    durations_path = Path(a.durations)
    old = load_durations(durations_path)
    parts = plan(mods, start, old,
                 a.split_over if a.split_over > 0 else float("inf"), a.jobs)
    jobs = min(a.jobs, max(1, len(parts)))
    print(f"{expected} tests in {len(mods)} modules, {len(parts)} processes, "
          f"{jobs} at a time (collect {time.monotonic() - t0:.1f}s)",
          flush=True)

    results = {}
    lock = threading.Lock()
    done = [0]
    step = max(1, len(parts) // 10)

    def work(i, part):
        name, mod, want, _guess = part
        if _stopping.is_set():
            return
        tmp = root / f"p{i:04d}"
        tmp.mkdir()
        cmd = [py, "-m", "unittest"] + ([preload] if preload else []) + [name]
        rc, text, secs = run_proc(cmd, start, child_env(tmp), a.timeout,
                                  log=root / f"p{i:04d}.log")
        shutil.rmtree(tmp, ignore_errors=True)
        ran, status = parse_result(text)
        if rc is None:
            why = f"TIMEOUT after {a.timeout:g}s"
        elif rc != 0:
            why = status or f"exit {rc}"
        elif ran != want:
            why = f"ran {ran} tests, holds {want}"
        else:
            why = ""
        with lock:
            results[name] = (mod, want, ran, secs, why, text)
            done[0] += 1
            if why or a.verbose or done[0] % step == 0 or done[0] == len(parts):
                tag = "FAIL" if why else "ok"
                print(f"[{done[0]:>{len(str(len(parts)))}}/{len(parts)}] "
                      f"{tag:<4} {secs:6.1f}s  {name} ({ran if ran is not None else '?'}/{want})"
                      + (f"  {why}" if why else ""), flush=True)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(work, i, p) for i, p in enumerate(parts)]
        for f in futs:
            f.result()
    wall = time.monotonic() - t0

    failed = {n: r for n, r in results.items() if r[4]}
    missing = [p[0] for p in parts if p[0] not in results]
    ran_total = sum(r[2] or 0 for r in results.values())
    new = {n: round(r[3], 2) for n, r in results.items() if not r[4]}
    # A module's time = the sum of its parts (what an unsplit run costs).
    per_mod = {}
    for n, r in results.items():
        per_mod[r[0]] = per_mod.get(r[0], 0.0) + r[3]
    for m, s in per_mod.items():
        if all(not r[4] for r in results.values() if r[0] == m):
            new[m] = round(s, 2)
    save_durations(durations_path, old, new)

    for n, (mod, want, ran, secs, why, text) in sorted(failed.items()):
        print(f"\n{'=' * 70}\nFAILED: {n} — {why}\n{'=' * 70}\n{text.rstrip()}")
    cpu = sum(r[3] for r in results.values())
    print(f"\nslowest {SLOWEST} modules (seconds, all parts summed):")
    for m, s in sorted(per_mod.items(), key=lambda kv: -kv[1])[:SLOWEST]:
        nparts = sum(1 for r in results.values() if r[0] == m)
        print(f"  {s:7.1f}  {m}" + (f"  ({nparts} parts)" if nparts > 1 else ""))
    skipped = sum(skipped_of(r[5]) for r in results.values())
    print(f"\nRan {ran_total} of {expected} tests"
          + (f" ({skipped} skipped)" if skipped else "")
          + f" in {wall:.1f}s on {jobs} "
          f"processes ({cpu:.0f}s of process time, {cpu / wall:.1f}x)")
    ok = not failed and not missing and ran_total == expected
    if ran_total != expected and not failed:
        print(f"COUNT MISMATCH: ran {ran_total}, collected {expected}")
    if missing:
        print(f"NOT RUN: {', '.join(missing)}")
    if failed:
        print(f"FAILED ({len(failed)} of {len(parts)} parts): "
              + ", ".join(sorted(failed)))
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

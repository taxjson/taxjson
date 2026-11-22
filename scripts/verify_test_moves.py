#!/usr/bin/env python3
"""verify_test_moves: the moves from BASE to HEAD changed nothing but
names (PLAN.md step 9).

Usage: scripts/verify_test_moves.py [BASE [HEAD]] [--no-ids] [--trees] [--repo=PATH]

HEAD is the batch's commit (default HEAD), BASE its base and an ancestor
of it (default: HEAD's merge base with origin/main). Both are read from
git, never from the work tree: what changed by `git diff-tree` (modes
included), each text from its blob, the runs in `git archive` exports,
with replace objects off and git's GIT_ variables dropped (a repository
with replace refs or grafts is refused). The proof is BASE's copy, from
git, isolated (-I -S: no site-packages, so no editable install's src;
no script folder or cwd on the path), never the checkout's:

    T=$(mktemp -d) && git --no-replace-objects archive BASE \
      scripts/move_tests.py scripts/verify_test_moves.py scripts/moved.py \
      scripts/extract_support.py |
      tar -x -C "$T" && python3 -I -S "$T/scripts/verify_test_moves.py" \
      BASE HEAD --repo=.

(The copy invoked runs BASE's copy again, with -I -S and no PYTHONPATH,
but a copy run from the checkout is the batch's and may import the
batch's files: only the archive route is the proof.) It proves an honest
batch's moves: the text check runs first, and a batch that fails it has
none of its code run; one that passes it is BASE's code renamed, which
the pins, wiring, ids and outcomes checks then run. It is no sandbox for a batch
written to fool it. The moves are HEAD's `Formerly:` lines
(scripts/move_tests.py) that name a module BASE has, one each; a module
a class's line names (`Formerly: OLD.Class`) was split (split_moves).
  (In the order run: 2 first, and only when it passes 3, 4, 1 and 5.)
  1. ids: unittest's discovery in both exports; HEAD's test ids are
     BASE's with each moved module renamed, one to one (--no-ids skips
     it: it imports every test module twice, about two minutes; a split's
     are counted in its outcome runs whatever the flag);
  2. text: every file the batch changed is, byte for byte and with its
     mode, BASE's under the mover's renames (a path everywhere, the dotted
     name where a script runs a module, the ratchet's count, a moved
     module's `Formerly:` line, the baseline re-keyed), and every file it
     left alone is itself under them. Beyond them only a test module's
     docstrings, a .md doc outside any tests/ folder, and an old module
     name a batch settled by hand (that move's new dotted name or path in
     its place, outside src) may differ: those are listed, for review.
     Never other code, a comment, src or the tools (TOOLS). A new file is
     a moved module (at a new path, its old path gone, its name no other
     module's), a split's file or a new package's docstring-only
     __init__.py below tests/rules/ or tests/features/; no symlink or
     submodule. A split's module is gone, and BASE's mover plans the split
     HEAD's class lines make (split_plans: BASE's SplitPlan, its refusals
     included, from the archive copy with extract_support): each file is
     the plan's text byte for byte, its docstrings apart (listed when they
     differ; fragment_problems); every file names one module, each class
     line its own class of it (split_problems);
  3. pins: check_tax_rules' collect() gives each test, renamed, the same
     rule and @rule_absent ids (a split's class or helper that two files
     hold, in each); its --summary prints the same and exits 0 in both;
  4. wiring: the no-extras stage's modules (its --list: EXTRAS_TESTS and
     PLUGIN_TESTS) and the release-edits stage's are BASE's, renamed (a
     split's class by its line; a split module a stage's grep picked: the
     new files whose text its pattern picks, the others listed as leaving
     the stage, their tests in the suite still);
     mutation_audit's TEST_CMDS name them, renamed (a split's module by
     class only); the architecture baseline's per-file counts are BASE's,
     re-keyed, and a split's files' (split_baseline_problems): ruff's at
     most the hits of BASE's module on the lines each took (ruff run on
     BASE's file: the proof of a split needs ruff, the dev venv's
     python3; an import kept for what it runs may add F401), the
     suppressions' its own comments' (BASE's check_architecture), none
     left for the module;
  5. outcomes, a split's: what each test does and checks, observed
     (_RECORD, run_outcomes): its module run whole at BASE three times
     and each of its files at HEAD twice, two BASE runs before HEAD's
     and one after (so a value from the clock the BASE runs agree on is
     HEAD's too; one it moves between them is dropped), each in a
     process of its own (the hermetic runner's setup, an empty HOME and
     a TMPDIR of its own, offline, width 0, PYTHONHASHSEED 0, tempfile's
     names seeded from the part's name, in a child too through a
     sitecustomize on PYTHONPATH and the seed in the child's environment
     only). Recorded by sys.monitoring for each test (its setUp,
     tearDown and cleanups included), each class's setUpClass and
     tearDownClass and the module's setUpModule and tearDownModule (each
     HEAD file's copy against BASE's): its outcome (the kind, the
     exception's type, a skip's reason); the test-tree lines it runs,
     counted (a function's, and a class body run inside one; OLD's code
     keyed by qualname and offset, so a copy matches its original); its
     assert*/fail*/subTest and mock assert_* calls, each with its site
     and a digest per argument (a message among them); its child
     processes (subprocess.Popen, os.system: argv, returncode, the
     folder, a digest per variable of its environment and per line of
     the output it captured); the product lines it reaches (src/,
     packages/*/src, and test-tree code the import system calls, such as
     _hermetic's finder: it runs at each first import); the values it
     can read, as they stand at its start, kept where the code that ran
     names them (value_read: its co_names and identifier string
     constants): the module-level names of its module and the tests/
     helpers loaded, os.environ (environ, getenv), the working folder
     (getcwd, cwd), the decimal context (getcontext, localcontext,
     Decimal), its class's attributes along the MRO, the attributes of
     the loaded product modules the test tree holds or imports (in a
     function too; an lru_cache's cache_info), and which of those
     modules are loaded; each test-code function's return (or yield)
     value and each local, a digest each. Digests are of normalized
     reprs (TMPDIR, HOME, the trees, the module's name (a new file's and
     OLD's alike) and file, addresses, an int that is the id() of an
     object the value holds: placeholders), containers walked to LIMIT
     elements and DEPTH levels (an object with the default repr by its
     attributes, a dataclass by its fields, a StringIO by its value); a
     value of FINE elements or fewer also gets a finer record (fine):
     its digest with the digit runs masked, the runs, its pieces (a
     dict's keys, a sequence's elements, a text's lines, an object's
     attributes; PIECES at most), a short repr. Compared (part_problems),
     each part by itself: what all three BASE runs agree on must be the
     same in each HEAD run (a module-level name BASE read and HEAD lacks,
     its module loaded at the part's start, is ABSENT: a difference; one
     whose module is not loaded there is not compared, and counted per
     module; one of OLD's a new file lacks is ABSENT where the code looked
     it up on the module, or where a BASE run's value at the part's start
     is not what OLD's import left: each run records OLD's names' digests
     when its import ends; a constant is not compared, a split taking
     nothing from it, and the mover refuses a read of OLD's names by a
     string); the product lines every BASE run reached, each HEAD run
     must reach (a split takes earlier classes out of a test's process: a
     cache an earlier class filled is a miss, more lines). Where the BASE
     runs disagree, finer (_compare): a HEAD value that is one BASE run's
     is dropped, some BASE run lacking it (an odd run out: a leak's mark);
     else with the digit runs masked (the runs they agree on still
     compared; each one they disagree on must lie within their range
     widened by its spread (2 at least), HEAD running between them in
     time, or with the adjacent ones (a timestamp's fields, its second
     wrapping; each later one a fixed-width field of two integer digits
     at most) between theirs read together, one not numeric of their
     length and kinds of character: left out and listed, HEAD's values
     beside BASE's (a timestamp beside an hour); one outside is dropped);
     else piece by piece, each so;
     the assertions or
     children at a site, or an argv, the BASE runs agree on still
     compared when other sites' are not. What is left, and a cut digest,
     is dropped, the BASE runs' values shown, and refuses the split
     unless HEAD's scripts/step9-unstable.tsv accepts it (the test id,
     the part key, why), each acceptance listed for the batch's review;
     an accepted line count must still be nonzero where every BASE run's
     is, and within the BASE runs' range widened by 2 or a tenth. A
     refusal resting only on a loop's count differing by a few is
     labelled so: rerun it; the same counts again are real. Also: every
     test passing at BASE (base_problems: a pass, an expected failure, or
     a skip by `@unittest.skip` applied directly with a constant reason;
     a failure, an error, a test not run, a fixture's error or skip, or a
     skip elsewhere (a name a condition binds to unittest.skip among
     them), which where it runs may change what others see, refuses: prep
     the module or move it whole); the outcomes the same in every run;
     the ids one to one, a duplicate each time; the fixtures' errors; no
     test of a class no line places (one a call made); no test that
     traces itself (sys.settrace, sys.setprofile, sys.monitoring). Needs
     Python 3.12 (sys.monitoring): refused on an older one.
     The table's rules (scripts/step9-unstable.tsv; the batch's reviewer
     keeps them): 1. every reason is written by hand and names the
     unstable source and the line that makes it (`:NNN`; read_unstable
     refuses one that names neither: UNSTABLE_SOURCES), never generated;
     2. a `local` or `arg` row: the reviewer reads the test and confirms
     nothing else in that value can depend on an earlier class; 3. a
     `line` row only for a poll or deadline loop, the locals after it
     stable; 4. no `assertions` or `child processes` row (refused), and no
     `cut` row without reading the value: prep the module instead; 5. a
     row the verifier notes no BASE run dropped goes; 6. more than about
     five rows for one test: prep the test or move it whole; 7. every
     "digit runs left out" note is read: each run is a clock's, a pid's or
     a duration's, HEAD's value beside BASE's; 8. a row accepting a match
     (a HEAD value some BASE run lacks) says why the value is flaky at
     BASE; 9. a row for a digit run outside BASE's spread names what the
     number is (an inode, a thread ident, a pid) and the line that makes
     it.
     Its limits: a value reached through no name the code that ran reads
     (an attribute of an object a product function returns, a product
     class's attribute, a module the test tree neither holds nor imports)
     is seen only through what it changes, and a module not loaded at the
     part's start after the split (imported in the test) only through
     what it changes (counted); a digit run the BASE runs disagree on is
     compared only to their range, widened by its spread (an earlier
     class's change inside that is not seen), and a timestamp's later
     fields only to their width; a constant of OLD's a new
     file lacks, read through a helper by a means no lookup on the module
     shows (its `__dict__`), is not seen; a child process is
     compared by its argv, returncode, folder, environment and the output
     the test captured (communicate), not traced: a child's work that
     changes none of them, or output read from a pipe, is not seen, and a
     child whose environment drops PYTHONPATH gets random temp names (its
     digests fall to the BASE runs' agreement); a bare `assert`'s values
     are not recorded, only its lines and the outcome; a branch on the
     environment in test code (`shutil.which`, `try: import openpyxl`)
     takes the verifier's side only (the mover lists them per split
     module); multiprocessing and forked children are not recorded (a
     fork writes no record); a clock value that is not monotonic (the
     seconds of a minute) may agree in the BASE runs and not after; a
     value drawn from randomness an earlier class seeded is caught only
     when a HEAD run draws otherwise (one bit: three times in four); CI's
     per-class runs of a slow module and the serial fallback run tests
     with fewer or other predecessors than these file-per-process runs
     (as at BASE; a split makes per-class runs rarer); a test-tree
     function the import system calls first is compared as product code
     from then on (a set), even where a test calls it too.
--trees lets BASE and HEAD be trees (the tests' synthetic repositories,
which have no commits: no ancestry to check); --repo reads another
repository. The skip count is the batch's suite run's to compare
(TJ-3683). Exit 0 with a line per check; 1 with each difference; 2 when
the arguments name no batch to check, or the Python is older than 3.12.
"""
from __future__ import annotations

import ast
import collections
import concurrent.futures
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def _load(name: str):
    """A sibling script as a module (scripts/ is not a package): BASE's,
    when this copy is BASE's."""
    spec = importlib.util.spec_from_file_location(f"_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MT = _load("move_tests")
MV = _load("moved")
# The tools: a batch never changes them (a change is a commit of its own,
# reviewed), and the copies that run are BASE's.
TOOLS = MT.TOOLS

_COLLECT = r"""
import importlib, json, sys, unittest
sys.path.insert(0, ".")
importlib.import_module("_hermetic")
loader = unittest.TestLoader()
ids = []
def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            walk(t)
        else:
            ids.append(t.id())
walk(loader.discover(".", "test_*.py", top_level_dir="."))
json.dump({"ids": ids, "errors": [str(e)[:300] for e in loader.errors]}, open(sys.argv[1], "w"))
"""

# What reads a value at a part's start: code that ran and names it (its
# co_names and identifier string constants). The recorder keeps a value
# only so, and the comparison counts a value missing in a run that reads
# it as unset; both use this one function (the recorder gets its source).
_TRIGGERS = {"env": ["environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"],
             "cwd": ["getcwd", "getcwdb", "cwd", "chdir"],
             "decimal": ["getcontext", "localcontext", "setcontext", "Decimal", "decimal",
                         "Context"]}
_TRIG = {k: frozenset(v) for k, v in _TRIGGERS.items()}


def value_read(key, names, trig):
    """Whether code naming NAMES reads the value KEY: an environment
    variable when it names os.environ or getenv; the working folder,
    getcwd or Path.cwd; the decimal context, getcontext, localcontext or
    Decimal (its flags, `flags` too); a class attribute or a module-level
    name, its name; a product module's attribute, its name (and
    `cache_info` for an lru_cache's)."""
    fam, _, rest = key.partition("|")
    if fam == "<env>":
        return not names.isdisjoint(trig["env"])
    if fam == "<cwd>":
        return not names.isdisjoint(trig["cwd"])
    if fam == "<decimal>":
        return not names.isdisjoint(trig["decimal"]) and (not rest or rest in names)
    if fam == "<prod>":
        return all(x in names for x in rest.partition("|")[2].split("."))
    return rest in names


# What each test does and checks, in one process (the hermetic runner's
# setup first; argv[1] the config: out, names, record, tree, own, own_file,
# limit, depth, triggers, alias, by_hand): each test's outcome (pass,
# "fail: AssertionError", "error: KeyError", "skip: REASON", "expected
# failure: AssertionError", ...), a fixture's own by its description
# (`setUpClass (test_x.TestY): error: RuntimeError`), the ids in the order
# run, a duplicate each time; when recording, each part's record (parts:
# the test's id, `setUpClass Class`, `setUpModule` ... -> a record per run
# of it): its test-tree lines counted, the names its code reads, its
# assertions (each argument), children (each variable, each output line),
# product lines, the values it read and its functions' returns and each
# local, the digests cut (the module docstring's item 5); with by_hand,
# OLD's tests an unconditional `@unittest.skip` skips.
_RECORD = r'''import ast, collections, dataclasses, glob, hashlib, inspect, io, json, os, random, re
import reprlib, sys
import tempfile, threading
import types, unittest, unittest.mock, subprocess

CFG = json.load(open(sys.argv[1], encoding="utf-8"))
PID = os.getpid()
SEED_VAR = "_VERIFY_TEMP_SEED"
SEED = ["<startup>"]


def seed(key):
    """tempfile's names from KEY: the same in each run of the same part. A
    child gets KEY in its own environment (_RecordedPopen), never through
    os.environ, which a test may read."""
    seq = tempfile._RandomNameSequence()
    rng = random.Random()
    rng.seed(f"verify:{key}")
    seq._rng, seq._rng_pid = rng, os.getpid()
    tempfile._name_sequence = seq
    SEED[0] = key


seed("<startup>")
os.environ.pop(SEED_VAR, None)
sys.path.insert(0, ".")
__import__("_hermetic")
RECORD = CFG["record"]
TREE = CFG["tree"]
OWN, OWN_FILE = CFG["own"], CFG["own_file"]
TRIG = {k: frozenset(v) for k, v in CFG.get("triggers", {}).items()}


def _forms(p):
    return {p, os.path.realpath(p)} if p else set()


# Placeholders for what differs between runs: the longest first.
_REPL = []
for _p, _ph in ((OWN_FILE, "<OWNFILE>"), (os.environ.get("HOME", ""), "<HOME>"),
                (tempfile.gettempdir(), "<TMP>"), (TREE, "<TREE>"),
                (os.path.dirname(TREE), "<TD>"), (sys.executable, "<PY>")):
    for _f in _forms(_p):
        if len(_f) > 1:
            _REPL.append((_f, _ph))
_REPL.sort(key=lambda x: -len(x[0]))
_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_MID = re.compile(r"\bid='\d+'")
_MOD = re.compile(r"(?<![\w.])(?:" + "|".join(
    re.escape(n) for n in sorted({OWN, CFG.get("alias") or OWN}, key=len, reverse=True))
    + r")(?!\w)")
_MODS = tuple({OWN, CFG.get("alias") or OWN})


def norm(s):
    if "/" in s:
        for a, b in _REPL:
            if a in s:
                s = s.replace(a, b)
    if any(n in s for n in _MODS):
        s = _MOD.sub("<MOD>", s)
    if "0x" in s:
        s = _ADDR.sub("0x?", s)
    if "id='" in s:
        s = _MID.sub("id='?'", s)
    return s


TESTS = tuple(f + os.sep for f in _forms(os.path.join(TREE, "tests")))
# The product: src/ and each package's src/ (packages/*/src), by their path in the tree.
_SRC_REL = []
for _d in [os.path.join(TREE, "src"), *sorted(glob.glob(os.path.join(TREE, "packages", "*", "src")))]:
    for _f in _forms(_d):
        _SRC_REL.append((_f + os.sep, os.path.relpath(_d, TREE).replace(os.sep, "/") + "/"))
SRC = tuple(p for p, _r in _SRC_REL)
LIMIT = CFG.get("limit", 100000)
DEPTH = CFG.get("depth", 60)
LEAF = (str, bytes, int, float, bool, type(None), complex)
BUSY = [0]
LASTCUT = [None]
LASTN = [0]
# A value's finer record (for a part the BASE runs may disagree on): its
# digest with the digit runs masked, the runs (RUNS at most; a piece's,
# PIECE_RUNS), its pieces (PIECES at most) and a short repr; none past FINE
# elements.
RUNS, PIECE_RUNS, PIECES, FINE = 256, 32, 256, 2000
_RUNB = re.compile(rb"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_REPR = reprlib.Repr()
_REPR.maxstring = _REPR.maxother = 60


def _plain(o):
    """An object with the default repr: its state is what it holds."""
    return type(o).__repr__ is object.__repr__ and not isinstance(o, type)


def _state(o):
    out = {}
    try:
        out.update(vars(o))
    except TypeError:
        pass
    for k in type(o).__mro__:
        slots = getattr(k, "__slots__", ())
        for s in ([slots] if isinstance(slots, str) else slots):
            if s not in out and s not in ("__dict__", "__weakref__"):
                try:
                    out[s] = getattr(o, s)
                except AttributeError:
                    pass
    return out


def _walk(o, h, st, depth, path):
    t = type(o)
    if t is int and o in st[2]:
        # the id() of an object the value holds (a map keyed by id(row)):
        # an address, other in each run
        h.update(b"<id>\0")
        return
    if t is str:
        h.update(b"s" + norm(o).encode("utf-8", "surrogatepass") + b"\0")
        return
    if t is bytes:
        h.update(b"b" + norm(o.decode("latin-1")).encode("latin-1", "replace") + b"\0")
        return
    if t in LEAF:
        h.update(repr(o).encode() + b"\0")
        return
    st[0] -= 1
    name = getattr(t, "__qualname__", "?").encode()
    if st[0] < 0 or depth > DEPTH:
        st[1].add("depth" if depth > DEPTH else "size")
        try:
            n = len(o)
        except Exception:
            n = -1
        h.update(b"<" + name + b":" + str(n).encode() + b">")
        return
    i = id(o)
    if i in path:
        h.update(b"<cycle>")
        return
    path.add(i)
    st[2].add(i)
    try:
        if isinstance(o, (list, tuple, collections.deque)):
            h.update(name + b"[" + str(len(o)).encode() + b"]")
            for x in list(o):
                _walk(x, h, st, depth + 1, path)
        elif isinstance(o, dict):
            h.update(name + b"{" + str(len(o)).encode() + b"}")
            for v in list(o.values()):         # a key may be a value's id()
                st[2].add(id(v))
                if type(v) in (tuple, list):
                    st[2].update(id(x) for x in v[:8])
                elif type(v) is dict:
                    st[2].update(id(x) for x in list(v.values())[:8])
            for k, v in list(o.items()):
                _walk(k, h, st, depth + 1, path)
                _walk(v, h, st, depth + 1, path)
        elif isinstance(o, (set, frozenset)):
            subs = []
            for x in list(o):
                g = hashlib.blake2b(digest_size=10)
                _walk(x, g, st, depth + 1, path)
                subs.append(g.digest())
            h.update(name + b"(" + b"".join(sorted(subs)) + b")")
        elif isinstance(o, BaseException):
            h.update(name + b"!")
            _walk(o.args, h, st, depth + 1, path)
        elif isinstance(o, (io.StringIO, io.BytesIO)):
            h.update(name + b"~")
            try:
                v = o.getvalue()
            except ValueError:
                v = "<closed>"
            _walk(v, h, st, depth + 1, path)
        elif _plain(o):
            h.update(name + b"@")
            _walk(_state(o), h, st, depth + 1, path)
        elif dataclasses.is_dataclass(o) and not isinstance(o, type):
            # its repr's fields, walked (a map in it keyed by id() a placeholder)
            h.update(name + b"%")
            _walk({f.name: getattr(o, f.name, None) for f in dataclasses.fields(o) if f.repr},
                  h, st, depth + 1, path)
        else:
            try:
                r = repr(o)
            except Exception as e:
                r = f"<{t.__qualname__}: repr {type(e).__name__}>"
            h.update(b"r" + norm(r).encode("utf-8", "surrogatepass") + b"\0")
    except RuntimeError:
        h.update(b"<changing>")
    finally:
        path.discard(i)


def dig(o):
    """O's digest: its normalized repr, containers walked (LIMIT items,
    DEPTH levels); LASTCUT says which cut it, if one did."""
    h = hashlib.blake2b(digest_size=10)
    st = [LIMIT, set(), set()]
    _walk(o, h, st, 0, set())
    LASTCUT[0] = "/".join(sorted(st[1])) or None
    LASTN[0] = LIMIT - st[0]
    return h.hexdigest()


class _Masked:
    """A hasher of what it is fed with each digit run masked (`12`,
    `3.25`, `6.2e-05`), keeping the runs (None past CAP)."""
    __slots__ = ("h", "runs", "cap")

    def __init__(self, cap):
        self.h, self.runs, self.cap = hashlib.blake2b(digest_size=10), [], cap

    def update(self, b):
        if self.runs is not None:
            got = _RUNB.findall(b)
            if got:
                if len(self.runs) + len(got) <= self.cap:
                    self.runs.extend(x.decode() for x in got)
                else:
                    self.runs = None
        self.h.update(_RUNB.sub(b"#", b))


def _masked(o, cap=RUNS):
    m = _Masked(cap)
    _walk(o, m, [LIMIT, set(), set()], 0, set())
    return m.h.hexdigest(), m.runs


def _show(o):
    """O's short repr (a text's value, an object's state abridged)."""
    if isinstance(o, (io.StringIO, io.BytesIO)):
        try:
            o = o.getvalue()
        except ValueError:
            return "<closed>"
    try:
        s = norm(_REPR.repr(o))
    except Exception as e:
        s = f"<{type(o).__qualname__}: repr {type(e).__name__}>"
    return s if len(s) <= 60 else s[:57] + "..."


def _pieces(o):
    """O's pieces, each (its name, its value): a text's lines, a dict's
    items by key, a sequence's elements, a dataclass's or a plain
    object's attributes. None for a value of none, or of more than PIECES."""
    if isinstance(o, (io.StringIO, io.BytesIO)):
        o = o.getvalue()
    if isinstance(o, (str, bytes)):
        out = [(f"line {i}", x) for i, x in enumerate(o.splitlines(), 1)] \
            if ("\n" if isinstance(o, str) else b"\n") in o else None
    elif isinstance(o, dict):
        out, seen = [], set()
        for k, v in list(o.items()):
            n = f"[{_show(k)}]"
            while n in seen:
                n += "'"
            seen.add(n)
            out.append((n, v))
    elif isinstance(o, (list, tuple, collections.deque)):
        out = [(f"[{i}]", x) for i, x in enumerate(list(o))]
    elif dataclasses.is_dataclass(o) and not isinstance(o, type):
        out = [(f".{f.name}", getattr(o, f.name, None)) for f in dataclasses.fields(o) if f.repr]
    elif _plain(o):
        out = [(f".{k}", v) for k, v in _state(o).items()]
    else:
        out = None
    return out if out is not None and len(out) <= PIECES else None


def fine(o, n):
    """O's finer record (N the elements its digest walked): [its digest
    with the digit runs masked, the runs, its pieces (each [name, digest,
    masked digest, runs, short repr]) or None, its short repr]; past FINE
    elements only the repr."""
    try:
        if n > FINE:
            return [None, None, None, _show(o) if type(o) in LEAF else None]
        m, runs = _masked(o)
        ps = _pieces(o)
        pieces = None if ps is None else [[k, dig(x), *_masked(x, PIECE_RUNS), _show(x)]
                                          for k, x in ps]
        return [m, runs, pieces, _show(o)]
    except Exception:
        return None


# --- the parts of a test, a fixture ---
STACK = []
CUR = None
PARTS = {}
FORKED = [False]


class Part:
    __slots__ = ("key", "lines", "asserts", "children", "prod", "values", "vcuts", "vfine",
                 "returns", "rfine", "cuts", "tracer", "loaded", "looked", "missing")

    def __init__(self, key):
        self.key, self.lines, self.asserts, self.children = key, {}, [], []
        self.prod, self.values, self.vcuts, self.returns = set(), {}, {}, {}
        self.vfine, self.rfine, self.cuts, self.tracer = {}, {}, {}, False
        self.loaded, self.looked, self.missing = [], frozenset(), set()


def _data(v):
    return not (callable(v) or hasattr(type(v), "__get__") or isinstance(v, types.ModuleType))


_SKIP_ATTRS = {"_classSetupFailed", "tearDown_exceptions", "_class_cleanups"}
_IMM = {}


def _value(v):
    """A value's digest, or None for one that holds no data a test reads
    (a module, a callable from outside the test tree)."""
    LASTCUT[0] = None
    if isinstance(v, types.ModuleType):
        return None
    if isinstance(v, (type, types.FunctionType)):
        f = getattr(sys.modules.get(getattr(v, "__module__", None) or ""), "__file__", None) or ""
        if not f.startswith(TESTS):
            return None
        if isinstance(v, type):
            return dig({k: x for k, x in vars(v).items()
                        if not k.startswith("__") and k not in _SKIP_ATTRS and _data(x)})
        cells = []
        for c in v.__closure__ or ():
            try:
                cells.append(c.cell_contents)
            except ValueError:
                cells.append("<empty>")
        return dig((v.__defaults__, v.__kwdefaults__, cells))
    if callable(v):
        return None
    if type(v) in LEAF or type(v) is frozenset or type(v) is tuple and all(
            type(x) in LEAF for x in v):
        hit = _IMM.get(id(v))
        if hit is not None and hit[0] is v:
            LASTCUT[0], LASTN[0] = hit[2], hit[3]
            return hit[1]
        d = dig(v)
        _IMM[id(v)] = (v, d, LASTCUT[0], LASTN[0])
        return d
    return dig(v)


def _modules():
    own = sys.modules.get(OWN)
    out = [("<own>", own)] if own is not None else []
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if m is own or not f.startswith(TESTS) or name.rpartition(".")[2].startswith("test"):
            continue
        out.append((name, m))
    return out


_NAMES_OF = {}


def _code_names(code):
    """The names CODE reads: its co_names and its string constants that
    are identifiers (`getattr(helper, "SEEN")` reads SEEN)."""
    s = _NAMES_OF.get(code)
    if s is None:
        s = _NAMES_OF[code] = frozenset(code.co_names) | {
            c for c in code.co_consts if type(c) is str and c.isidentifier()}
    return s


_FILE_NAMES = {}


def _file_names(f):
    """FILE's names its code reads, and the modules it imports anywhere (a
    function's `from taxjson.x import y` too: `taxjson.x`, `taxjson.x.y`);
    each file read once."""
    got = _FILE_NAMES.get(f)
    if got is None:
        names, mods = set(), set()
        try:
            src = open(f, encoding="utf-8").read()
            stack = [compile(src, f, "exec", dont_inherit=True)]
            tree = ast.parse(src)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            stack, tree = [], None
        while stack:
            c = stack.pop()
            names |= _code_names(c)
            stack += [x for x in c.co_consts if isinstance(x, types.CodeType)]
        for n in ast.walk(tree) if tree is not None else ():
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods |= {n.module, *(f"{n.module}.{a.name}" for a in n.names)}
        got = _FILE_NAMES[f] = (frozenset(names), frozenset(mods))
    return got


def _names_any():
    """Every name the loaded test-tree modules' code reads: which of a
    product module's attributes to digest."""
    out = set()
    for _mk, m in _modules():
        f = getattr(m, "__file__", None)
        if f:
            out |= _file_names(f)[0]
    return out


def _prod_modules():
    """The product modules loaded that the test tree's modules hold by
    name or import (in a function too), and their submodules held as
    attributes (four levels)."""
    out, todo = {}, []
    for _mk, m in _modules():
        todo += [(v, 0) for v in list(vars(m).values()) if isinstance(v, types.ModuleType)]
        f = getattr(m, "__file__", None)
        if f:
            todo += [(sys.modules[n], 0) for n in _file_names(f)[1] if n in sys.modules]
    while todo:
        m, d = todo.pop()
        f = getattr(m, "__file__", None) or ""
        if id(m) in out or not f.startswith(SRC):
            continue
        out[id(m)] = m
        if d < 4:
            todo += [(v, d + 1) for v in list(vars(m).values())
                     if isinstance(v, types.ModuleType) and v.__name__.startswith(m.__name__ + ".")]
    return list(out.values())


_FIMM = {}


def _put(p, key, f, v, finer=True):
    LASTN[0] = 0
    try:
        d = f(v)
    except Exception as e:
        d, LASTCUT[0] = f"<{type(e).__name__}>", None
    if d is not None:
        p.values[key] = d
        if LASTCUT[0]:
            p.vcuts[key] = LASTCUT[0]
        elif finer and not isinstance(v, (type, types.FunctionType)) and d[0] != "<":
            hit = _FIMM.get(id(v))
            if hit is not None and hit[0] is v:
                p.vfine[key] = hit[1]
            else:
                p.vfine[key] = rec = fine(v, LASTN[0])
                if type(v) in LEAF or type(v) is frozenset or type(v) is tuple and all(
                        type(x) in LEAF for x in v):
                    _FIMM[id(v)] = (v, rec)   # an immutable value's: once


def _snapshot(p, cls):
    """The values a part may read, as they stand at its start (kept at its
    end only where the code that ran names them, value_read): the
    module-level names of its module and the tests/ helpers loaded; the
    environment, the working folder and the decimal context; its class's
    attributes along the MRO; the attributes of the product modules the
    test tree holds or imports that its code names (an lru_cache's
    cache_info). Also which of those modules are loaded (LOADED) and the
    names looked for in a product module (LOOKED): a value one of them
    should hold and does not is ABSENT. A finer record (fine) for each
    value the loaded code names."""
    names = _names_any()
    p.looked = frozenset(names)
    for mk, m in _modules():
        p.loaded.append(mk)
        for n, v in list(vars(m).items()):
            if not n.startswith("__"):
                _put(p, f"{mk}|{n}", _value, v, n in names)
    for k, v in list(os.environ.items()):
        if k != SEED_VAR:
            _put(p, f"<env>|{k}", dig, v)
    try:
        _put(p, "<cwd>", dig, os.getcwd())
    except OSError:
        p.values["<cwd>"] = "<gone>"
    dec = sys.modules.get("decimal")
    if dec is not None:
        c = dec.getcontext()
        _put(p, "<decimal>", dig, (c.prec, str(c.rounding), c.Emin, c.Emax, c.capitals, c.clamp,
                                   sorted(k.__name__ for k, on in c.traps.items() if on)))
        _put(p, "<decimal>|flags", dig, sorted(k.__name__ for k, on in c.flags.items() if on))
    if cls is not None:
        seen = set()
        for k in cls.__mro__:
            for n, v in list(vars(k).items()):
                if n in seen or n.startswith("__") or n in _SKIP_ATTRS:
                    continue
                seen.add(n)
                if _data(v):
                    _put(p, f"<class>|{n}", _value, v, n in names)
    for m in _prod_modules():
        p.loaded.append(m.__name__)
        for n in names.intersection(vars(m)):
            v = vars(m)[n]
            if isinstance(v, types.ModuleType) or n.startswith("__"):
                continue
            if callable(v) and callable(getattr(v, "cache_info", None)):
                _put(p, f"<prod>|{m.__name__}|{n}.cache_info", lambda x: dig(tuple(x.cache_info())), v)
            elif _data(v):
                _put(p, f"<prod>|{m.__name__}|{n}", _value, v)


def begin(key, seed_key, cls=None):
    global CUR
    p = Part(key)
    if RECORD and not FORKED[0]:
        BUSY[0] += 1
        try:
            _snapshot(p, cls)
        finally:
            BUSY[0] -= 1
    STACK.append(p)
    CUR = p
    seed(seed_key)
    if RECORD and not FORKED[0]:
        _restart()


_KEYS = {}
_OWN_KEYS = {}


def _own_keys():
    """The own module's code objects (OLD's, or the new file's: compiled
    again, matched by equality) -> a key that does not depend on where the
    def sits in its file: its qualname, and for code inside a function its
    line offset from the outermost function and its index among the
    parent's code of that qualname (two lambdas on one line). A class body
    inside a function is keyed so too."""
    _OWN_KEYS[None] = None
    try:
        src = open(OWN_FILE, encoding="utf-8").read()
        top = compile(src, OWN_FILE, "exec", dont_inherit=True)
    except (OSError, SyntaxError, ValueError):
        return

    def walk(code, anchor):
        seen = {}
        for c in code.co_consts:
            if isinstance(c, types.CodeType):
                i = seen[c.co_qualname] = seen.get(c.co_qualname, -1) + 1
                if c.co_flags & inspect.CO_OPTIMIZED or anchor is not None:
                    off = c.co_firstlineno - anchor.co_firstlineno if anchor else 0
                    _OWN_KEYS.setdefault(c, f"<own>|{c.co_qualname}|{off}|{i}")
                    walk(c, anchor or c)
                else:
                    walk(c, anchor)
    walk(top, None)


def code_key(code):
    k = _KEYS.get(code)
    if k is None:
        f = code.co_filename
        if f in _forms(OWN_FILE):
            if not _OWN_KEYS:
                _own_keys()
            k = _OWN_KEYS.get(code) or f"<own>|{code.co_qualname}|@{code.co_firstlineno}"
        else:
            rel = f
            for t in TESTS:
                if f.startswith(t):
                    rel = "tests/" + f[len(t):]
            k = f"{rel}|{code.co_qualname}|{code.co_firstlineno}"
        _KEYS[code] = k
    return k


def line_key(code, line):
    return f"{code_key(code)}|{line - code.co_firstlineno}"


def _rel(f):
    for s, r in _SRC_REL:
        if f.startswith(s):
            return r + f[len(s):]
    for s in TESTS:
        if f.startswith(s):
            return "tests/" + f[len(s):]
    return f


def end():
    global CUR
    p = STACK.pop()
    CUR = STACK[-1] if STACK else None
    if FORKED[0]:
        return
    BUSY[0] += 1
    try:
        lines, names = {}, set()
        for (code, line), n in list(p.lines.items()):
            k = line_key(code, line)
            lines[k] = lines.get(k, 0) + n
            names |= _code_names(code)
        prod = {}
        for f, line in p.prod:
            prod.setdefault(_rel(f), []).append(line)
        cuts = dict(p.cuts)
        values, vfine = {}, {}
        for k, d in p.values.items():
            if value_read(k, names, TRIG):
                values[k] = d
                if k in p.vcuts:
                    cuts[f"value {k}"] = p.vcuts[k]
                if p.vfine.get(k) is not None:
                    vfine[k] = p.vfine[k]
        rec = {
            "lines": lines,
            "names": sorted(names),
            "asserts": [[m, line_key(*site) if site else "<other>", args]
                        for m, site, args in p.asserts],
            "children": [[argv, (proc.returncode if proc is not None else rc), cwd, env, out, err,
                          ofine, efine]
                         for argv, proc, rc, cwd, env, out, err, ofine, efine in p.children],
            "product": {f: sorted(set(v)) for f, v in sorted(prod.items())},
            "values": values,
            "fine": vfine,
            "returns": p.returns,
            "rfine": p.rfine,
            "cuts": cuts,
            "tracer": p.tracer,
            "loaded": sorted(set(p.loaded)),
            "unseen": sorted(names - p.looked),
            "missing": sorted(p.missing)}
        PARTS.setdefault(p.key, []).append(rec)
    finally:
        BUSY[0] -= 1


# --- what a test calls, recorded ---
_LOCAL = threading.local()


def _site(frame):
    code = frame.f_code
    return (code, frame.f_lineno) if KIND.get(code) is TEST else None


def _args(p, a, k):
    """Each argument's digest (`1`, `2`, ... and the keywords by name, a
    message among them), a cut kept by where it was."""
    i = len(p.asserts) + 1
    out = []
    for n, v in [*((str(j), x) for j, x in enumerate(a, 1)), *sorted(k.items())]:
        d = dig(v)
        if LASTCUT[0]:
            p.cuts[f"assertion #{i} arg {n}"] = LASTCUT[0]
        out.append([n, d, fine(v, LASTN[0]) if i <= 2000 and not LASTCUT[0] else None])
    return out


def _calling(name, f):
    def call(*a, **k):
        p = CUR
        if p is None or FORKED[0] or getattr(_LOCAL, "depth", 0):
            return f(*a, **k)
        _LOCAL.depth = 1
        try:
            BUSY[0] += 1
            try:
                p.asserts.append((name, _site(sys._getframe(1)), _args(p, a[1:], k)))
            finally:
                BUSY[0] -= 1
            return f(*a, **k)
        finally:
            _LOCAL.depth = 0
    call.__name__, call.__qualname__, call.__wrapped__ = f.__name__, f.__qualname__, f
    call.__doc__ = f.__doc__
    return call


for _n, _f in list(vars(unittest.TestCase).items()):
    if (_n.startswith("assert") or _n.startswith("fail") or _n == "subTest") and \
            inspect.isfunction(_f):
        setattr(unittest.TestCase, _n, _calling(_n, _f))
for _cls in (unittest.mock.NonCallableMock, getattr(unittest.mock, "AsyncMockMixin", None)):
    for _n, _f in list(vars(_cls or object).items()):
        if _cls is not None and _n.startswith("assert_") and inspect.isfunction(_f):
            setattr(_cls, _n, _calling(_n, _f))


def _argv(args):
    if isinstance(args, (str, bytes, os.PathLike)):
        return [norm(os.fsdecode(args))]
    out = []
    for a in args:
        out.append(norm(os.fsdecode(a)) if isinstance(a, (str, bytes, os.PathLike)) else repr(a))
    return out


def _env_of(env):
    """A child's environment, a digest per variable (the seed apart)."""
    out = {}
    for k, v in env.items():
        k = os.fsdecode(k)
        if k != SEED_VAR:
            out[k] = dig(os.fsdecode(v))
    return out


def _cwd_of(cwd):
    try:
        return norm(os.fsdecode(cwd)) if cwd is not None else norm(os.getcwd())
    except OSError:
        return "<gone>"


def _lines(o):
    """Captured output, a digest per line."""
    if o is None:
        return None
    if isinstance(o, bytes):
        o = o.decode("latin-1")
    return [dig(x) for x in o.splitlines()]


def _lines_fine(o):
    """Captured output, a finer record per line (400 lines at most)."""
    if o is None:
        return None
    if isinstance(o, bytes):
        o = o.decode("latin-1")
    lines = o.splitlines()
    return [[*_masked(x, PIECE_RUNS), None, _show(x)] for x in lines] if len(lines) <= 400 \
        else None


_Popen = subprocess.Popen


class _RecordedPopen(_Popen):
    def __init__(self, args, *a, **k):
        p = CUR
        if p is None or FORKED[0]:
            super().__init__(args, *a, **k)
            return
        a = list(a)
        env = a[9] if len(a) > 9 else k.get("env")
        env = dict(os.environ if env is None else env)
        if any(isinstance(x, bytes) for x in env):
            env[os.fsencode(SEED_VAR)] = os.fsencode(SEED[0])
        else:
            env[SEED_VAR] = SEED[0]
        if len(a) > 9:
            a[9] = env
        else:
            k["env"] = env
        cwd = a[8] if len(a) > 8 else k.get("cwd")
        BUSY[0] += 1
        try:
            rec = [_argv(args), None, None, _cwd_of(cwd), _env_of(env), None, None, None, None]
        finally:
            BUSY[0] -= 1
        super().__init__(args, *a, **k)
        rec[1] = self
        self._verify = rec
        p.children.append(rec)

    def communicate(self, *a, **k):
        out, err = super().communicate(*a, **k)
        r = getattr(self, "_verify", None)
        if r is not None:
            BUSY[0] += 1
            try:
                r[5], r[6], r[7], r[8] = _lines(out), _lines(err), _lines_fine(out), \
                    _lines_fine(err)
            finally:
                BUSY[0] -= 1
        return out, err


subprocess.Popen = _RecordedPopen
_system = os.system


def _recorded_system(command):
    p = CUR
    if p is None or FORKED[0]:
        return _system(command)
    BUSY[0] += 1
    try:
        rec = [_argv(command), None, None, _cwd_of(None),
               _env_of({k: v for k, v in os.environ.items()}), None, None, None, None]
    finally:
        BUSY[0] -= 1
    # The child's seed, in the environment for the call only.
    os.environ[SEED_VAR] = SEED[0]
    try:
        rc = _system(command)
    finally:
        os.environ.pop(SEED_VAR, None)
    rec[2] = rc
    p.children.append(rec)
    return rc


os.system = _recorded_system


# --- sys.monitoring ---
M = sys.monitoring
E = M.events
DISABLE = M.DISABLE
TEST, PROD, HOOK, OTHER = "test", "prod", "hook", "other"
KIND = {}
_set_local, _restart, _register = M.set_local_events, M.restart_events, M.register_callback
TOOL = None


def _classify(code, caller):
    """TEST (lines counted, returns and locals digested) for test-tree
    code: a function's, and a class body that runs inside one. PROD (a set
    of lines) for src/ and packages/*/src. Test-tree code the import system
    calls first (a meta_path finder such as _hermetic's, and what it calls)
    is HOOK, a set like PROD: it runs at each first import, which a split
    makes more of, as it makes more cache misses."""
    f = code.co_filename
    if f.startswith(TESTS):
        if not code.co_flags & inspect.CO_OPTIMIZED:
            if caller is not None and KIND.get(caller) is TEST and code.co_name != "<module>":
                KIND[code] = TEST
                _set_local(TOOL, code, E.LINE | E.PY_RETURN)
            else:
                KIND[code] = OTHER
        elif caller is not None and (caller.co_filename.startswith("<frozen importlib")
                                     or KIND.get(caller) is HOOK):
            KIND[code] = HOOK
            _set_local(TOOL, code, E.LINE)
        else:
            KIND[code] = TEST
            _set_local(TOOL, code, E.LINE | E.PY_RETURN | E.PY_YIELD)
    elif f.startswith(SRC):
        KIND[code] = PROD
        _set_local(TOOL, code, E.LINE)
    else:
        KIND[code] = OTHER


def _on_start(code, offset):
    if code not in KIND:
        try:
            back = sys._getframe(1).f_back
        except ValueError:
            back = None
        _classify(code, back.f_code if back is not None else None)
    return DISABLE


def _on_line(code, line):
    if KIND.get(code) is TEST:
        p = CUR
        if p is not None and not BUSY[0]:
            k = (code, line)
            p.lines[k] = p.lines.get(k, 0) + 1
        return None
    if BUSY[0]:
        return None
    p = CUR
    if p is not None:
        p.prod.add((code.co_filename, line))
    return DISABLE


def _on_return(kind):
    def on(code, offset, value):
        p = CUR
        if p is None or BUSY[0] or KIND.get(code) is not TEST:
            return None
        BUSY[0] += 1
        try:
            try:
                local = dict(sys._getframe(1).f_locals)
            except Exception:
                local = {}
            first = local.get("__firstlineno__")
            if not code.co_flags & inspect.CO_OPTIMIZED and type(first) is int \
                    and first == code.co_firstlineno:
                # Python 3.13 sets a class body's __firstlineno__, its
                # code's first line (TJ-4214): a line, counted from there
                # as line_key counts one; where the class sits in its
                # function is in code_key.
                local["__firstlineno__"] = 0
            ck = code_key(code)
            for n, v in [("<value>", value), *sorted(local.items())]:
                d = dig(v)
                where = f"{ck}|{kind}|{n}"
                ds = p.returns.setdefault(where, {})
                ds[d] = ds.get(d, 0) + 1
                if LASTCUT[0]:
                    p.cuts.setdefault(f"local {where}", LASTCUT[0])
                # each call's finer record, in order (4 calls at most)
                fs = p.rfine.setdefault(where, [])
                if fs is not None:
                    p.rfine[where] = None if len(fs) >= 4 or LASTCUT[0] else \
                        fs + [[d, *(fine(v, LASTN[0]) or [None] * 4)]]
        finally:
            BUSY[0] -= 1
        return None
    return on


def _forked():
    FORKED[0] = True
    if TOOL is not None:
        for ev in (E.PY_START, E.LINE, E.PY_RETURN, E.PY_YIELD):
            _register(TOOL, ev, None)


if RECORD:
    TOOL = next(t for t in (M.PROFILER_ID, M.OPTIMIZER_ID, 4, 3) if M.get_tool(t) is None)
    M.use_tool_id(TOOL, "verify_test_moves")
    _register(TOOL, E.PY_START, _on_start)
    _register(TOOL, E.LINE, _on_line)
    _register(TOOL, E.PY_RETURN, _on_return("r"))
    _register(TOOL, E.PY_YIELD, _on_return("y"))
    M.set_events(TOOL, E.PY_START)
    os.register_at_fork(after_in_child=_forked)


# --- a test that traces itself: refused ---
def _flagging(f):
    def call(*a, **k):
        if CUR is not None and not (len(a) == 1 and a[0] is None and not k):
            CUR.tracer = True
        return f(*a, **k)
    call.__wrapped__ = f
    return call


for _mod, _n in ((sys, "settrace"), (sys, "setprofile"), (threading, "settrace"),
                 (threading, "setprofile"), (threading, "settrace_all_threads"),
                 (threading, "setprofile_all_threads"), (M, "use_tool_id"), (M, "set_events"),
                 (M, "set_local_events"), (M, "register_callback")):
    if hasattr(_mod, _n):
        setattr(_mod, _n, _flagging(getattr(_mod, _n)))


# --- the run ---
class Suite(unittest.TestSuite):
    """unittest's suite, each fixture a part of its own."""

    def _handleModuleFixture(self, test, result):
        if test.__class__.__module__ == self._get_previous_module(result):
            return super()._handleModuleFixture(test, result)
        begin("setUpModule", "setUpModule")
        try:
            return super()._handleModuleFixture(test, result)
        finally:
            end()

    def _handleModuleTearDown(self, result):
        if self._get_previous_module(result) is None:
            return super()._handleModuleTearDown(result)
        begin("tearDownModule", "tearDownModule")
        try:
            return super()._handleModuleTearDown(result)
        finally:
            end()

    def _handleClassSetUp(self, test, result):
        if test.__class__ == getattr(result, "_previousTestClass", None):
            return super()._handleClassSetUp(test, result)
        key = f"setUpClass {test.__class__.__qualname__}"
        begin(key, key, test.__class__)
        try:
            return super()._handleClassSetUp(test, result)
        finally:
            end()

    def _tearDownPreviousClass(self, test, result):
        prev = getattr(result, "_previousTestClass", None)
        if prev is None or prev == test.__class__:
            return super()._tearDownPreviousClass(test, result)
        key = f"tearDownClass {prev.__qualname__}"
        begin(key, key, prev)
        try:
            return super()._tearDownPreviousClass(test, result)
        finally:
            end()


def _by_hand():
    """OLD's tests an unconditional `@unittest.skip("reason")` skips (`Class`
    or `Class.test`): the decorator applied directly, its reason a
    constant: `unittest.skip`, the module imported by name (`import
    unittest [as ut]`), or `skip` imported from it (`from unittest import
    skip [as s]`), the name bound by nothing else in the module and
    resolved to unittest.skip itself (an alias of skipIf or skipUnless, a
    name a condition binds, is not one). They run nowhere, so they change
    what no class sees."""
    m = sys.modules.get(OWN)
    if m is None or not OWN_FILE:
        return []
    try:
        tree = ast.parse(open(OWN_FILE, encoding="utf-8").read())
    except (OSError, SyntaxError, ValueError):
        return []
    # a module-level name's bindings: an import of unittest or its skip, or else
    binds = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                k = a.asname or a.name.split(".")[0]
                top = a.name if a.asname else a.name.split(".")[0]
                binds.setdefault(k, set()).add("unittest" if top == "unittest" else "other")
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                binds.setdefault(a.asname or a.name, set()).add(
                    "skip" if n.module == "unittest" and a.name == "skip" and not n.level
                    else "other")
        elif isinstance(n, (ast.Name, ast.arg)) and not isinstance(getattr(n, "ctx", None),
                                                                     ast.Load):
            binds.setdefault(getattr(n, "id", None) or n.arg, set()).add("other")
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            binds.setdefault(n.name, set()).add("other")

    def direct(f):
        if isinstance(f, ast.Attribute) and f.attr == "skip" and isinstance(f.value, ast.Name):
            return binds.get(f.value.id) == {"unittest"} and vars(m).get(f.value.id) is unittest
        if isinstance(f, ast.Name):
            return binds.get(f.id) == {"skip"} and vars(m).get(f.id) is unittest.skip
        return False

    def bare(n):
        return any(isinstance(d, ast.Call) and len(d.args) == 1 and not d.keywords
                   and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)
                   and direct(d.func) for d in n.decorator_list)
    out = []
    for c in tree.body:
        if isinstance(c, ast.ClassDef):
            if bare(c):
                out.append(c.name)
            out += [f"{c.name}.{f.name}" for f in c.body
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and bare(f)]
    return out


loader = unittest.TestLoader()
suite = loader.loadTestsFromNames(CFG["names"])
flat = []


def _flatten(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            _flatten(t)
        else:
            flat.append(t)


_flatten(suite)
got = {t.id(): "not run" for t in flat}
# The own module's names as its import left them (IMPORTED: a digest each,
# what is not data aside), and a name looked up on it that it lacks
# (`getattr(sys.modules[...], "X", ())`), put on the part running: by a
# `__getattr__` on a subclass of the module's class, so `dir()` and
# `vars()` do not show it (a module that has its own has it wrapped).
IMPORTED = {}
_OWN_MOD = sys.modules.get(OWN)
if RECORD and _OWN_MOD is not None:
    BUSY[0] += 1
    try:
        for _n, _v in list(vars(_OWN_MOD).items()):
            if not _n.startswith("__"):
                _d = _value(_v)
                if _d is not None:
                    IMPORTED[_n] = _d
    finally:
        BUSY[0] -= 1

    def _lacks(name, _was=vars(_OWN_MOD).get("__getattr__")):
        if CUR is not None and not FORKED[0] and not name.startswith("__"):
            CUR.missing.add(name)
        if _was is not None:
            return _was(name)
        raise AttributeError(f"module {OWN!r} has no attribute {name!r}", name=name,
                             obj=_OWN_MOD)
    if "__getattr__" in vars(_OWN_MOD):
        _OWN_MOD.__getattr__ = _lacks
    else:
        try:
            _OWN_MOD.__class__ = type(type(_OWN_MOD).__name__, (type(_OWN_MOD),),
                                      {"__getattr__": lambda _m, name: _lacks(name),
                                       "__module__": type(_OWN_MOD).__module__,
                                       "__qualname__": type(_OWN_MOD).__qualname__})
        except TypeError:
            _OWN_MOD.__getattr__ = _lacks
ids, held = [t.id() for t in flat], []


class Result(unittest.TestResult):
    def startTest(self, t):
        super().startTest(t)
        rest = t.id()[len(type(t).__module__) + 1:]
        begin(t.id(), f"<MOD>.{rest}", type(t))

    def stopTest(self, t):
        end()
        super().stopTest(t)

    def put(self, t, what):
        if t.id() in got:
            got[t.id()] = what
        else:
            held.append(f"{getattr(t, 'description', t.id())}: {what}")

    def addSuccess(self, t): self.put(t, "pass")
    def addFailure(self, t, e): self.put(t, f"fail: {e[0].__name__}")
    def addError(self, t, e): self.put(t, f"error: {e[0].__name__}")
    def addSkip(self, t, why): self.put(t, f"skip: {why}")
    def addExpectedFailure(self, t, e): self.put(t, f"expected failure: {e[0].__name__}")
    def addUnexpectedSuccess(self, t): self.put(t, "unexpected success")

    def addSubTest(self, t, st, e):
        if e is not None:
            kind = "fail" if issubclass(e[0], t.failureException) else "error"
            self.put(t, f"{kind}: {e[0].__name__}")


Suite(flat).run(Result())
if os.getpid() == PID:
    if RECORD:
        M.set_events(TOOL, 0)
    with open(CFG["out"], "w", encoding="utf-8") as fh:
        json.dump({"tests": got, "ids": ids, "held": held, "parts": PARTS, "imported": IMPORTED,
                   "by_hand": _by_hand() if CFG.get("by_hand") else []}, fh)
'''
_RECORD = inspect.getsource(value_read) + "\n\n" + _RECORD

# A child's seeding (a test's subprocess with PYTHONPATH kept): its temp
# names from the part's seed and its argv, as the parent's are.
_SEED_SITE = r'''"""The proof's seeding of temp names (scripts/verify_test_moves.py)."""
import os
_key = os.environ.get("_VERIFY_TEMP_SEED")
if _key is not None:
    import random
    import sys
    import tempfile
    _seq = tempfile._RandomNameSequence()
    _rng = random.Random()
    _rng.seed("verify:" + _key + "\0" + "\0".join(sys.argv))
    _seq._rng, _seq._rng_pid = _rng, os.getpid()
    tempfile._name_sequence = _seq
'''
# The most elements a value's digest walks, and the deepest it goes (then
# its type and length: a cut, which counts as a part dropped).
LIMIT = 100000
DEPTH = 60

_PINS = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("ctr", "scripts/check_tax_rules.py")
K = importlib.util.module_from_spec(spec); spec.loader.exec_module(K)
files = sorted(p for p in K.TESTS.rglob("*.py") if K.RULES_DIR not in p.parents)
files += sorted((K.ROOT / "packages").glob("*/tests/**/*.py"))
out = {}
for m in K.collect(files):
    rel = m.path.relative_to(K.ROOT).as_posix()
    mod = rel[len("tests/"):-3].replace("/", ".") if rel.startswith("tests/") else rel
    name = (f"{m.cls.name}.{m.node.name}" if m.cls is not None and m.cls is not m.node
            else m.node.name)
    out.setdefault(f"{mod}.{name}", []).append(
        [sorted(i for ids, _ in m.rules for i in ids), sorted(list(a[:2]) for a in m.absent)])
json.dump(out, open(sys.argv[1], "w"), sort_keys=True)
"""


def _env(tree: Path, home: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAXJSON_")}
    env.update(PYTHONPATH=str(tree / "src"), HOME=home, TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return env


def _genv() -> Dict[str, str]:
    """The environment for git: replace objects off, so what it shows is
    the objects themselves (TJ-3723), and none of git's GIT_ variables
    (GIT_DIR, GIT_OBJECT_DIRECTORY, GIT_INDEX_FILE ... name another
    repository or its objects; TJ-3750)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_NO_REPLACE_OBJECTS": "1"}


def _git(repo: Path, *a: str) -> bytes:
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, check=True,
                          env=_genv()).stdout


def rewritten_history(repo: Path) -> Optional[str]:
    """Why git's view of REPO may not be its objects: replace refs, grafts."""
    if _git(repo, "for-each-ref", "--count=1", "refs/replace/").strip():
        return "the repository has replace refs (refs/replace/)"
    grafts = _git(repo, "rev-parse", "--git-path", "info/grafts").decode().strip()
    if (repo / grafts).exists():
        return f"the repository has grafts ({grafts})"
    return None


def resolve(repo: Path, rev: str) -> Tuple[str, str]:
    """REV's object id and kind: `commit` (a tag peeled) or `tree`."""
    for kind in ("commit", "tree"):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{{kind}}}"],
                           cwd=repo, capture_output=True, text=True, env=_genv())
        if r.returncode == 0:      # a commit is tried first: `X^{tree}` would peel it
            return r.stdout.strip(), kind
    raise SystemExit(f"verify: {rev!r} is neither a commit nor a tree")


def default_base(repo: Path, head: str) -> str:
    r = subprocess.run(["git", "merge-base", "origin/main", head], cwd=repo,
                       capture_output=True, text=True, env=_genv())
    if r.returncode:
        raise SystemExit("verify: HEAD has no merge base with origin/main: name BASE")
    return r.stdout.strip()


def ancestry_problem(repo: Path, base: str, head: str) -> Optional[str]:
    r = subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=repo, env=_genv())
    return None if r.returncode == 0 else \
        f"BASE {base[:12]} is not an ancestor of HEAD {head[:12]}"


def export(rev: str, dest: Path, repo: Path = REPO, paths: Tuple[str, ...] = ()) -> None:
    """REV's tree (or PATHS in it) into DEST, as a checkout writes it."""
    tar = _git(repo, "archive", "--format=tar", rev, *paths)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True)


def changes(repo: Path, base: str, head: str) -> List[Tuple[str, str, str, str, str, str]]:
    """(status, path, BASE's mode, HEAD's mode, BASE's blob, HEAD's blob)
    for each path that differs between the two trees."""
    parts = _git(repo, "diff-tree", "-r", "-z", "--raw", "--no-renames", base, head).split(b"\0")
    out = []
    for meta, path in zip(parts[0::2], parts[1::2]):
        if not meta:
            break
        om, nm, ob, nb, st = meta.decode()[1:].split()
        out.append((st[0], path.decode("utf-8", "surrogateescape"), om, nm, ob, nb))
    return out


def split_moves(base: Path, head: Path) -> Dict[str, List[str]]:
    """This batch's splits, from HEAD's class lines (`Formerly: OLD.Class`)
    naming a module BASE has at tests/: old module -> the new files below
    tests/ its classes went to."""
    return {old: rels for old, rels in MV.split_modules(MV.formerly_map(head / "tests")).items()
            if (base / "tests" / f"{old}.py").is_file()}


def batch_moves(base: Path, head: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """This batch's moves, from HEAD's `Formerly:` lines (exports at BASE
    and HEAD): old module -> new dotted module (whole files; not a split
    module, which its files all name), and old module.Class -> new dotted
    module (split classes)."""
    mods, classes = {}, {}
    split = split_moves(base, head)
    for old, places in MV.formerly_map(head / "tests").items():
        if not (base / "tests" / f"{old.split('.')[0]}.py").is_file():
            continue
        for rel, cls in places:
            d = MT.dotted(rel)
            if cls:
                classes[old] = d
            elif "." not in old and old not in split:
                mods[old] = d
    return mods, classes


def split_class_paths(head: Path, splits: Dict[str, List[str]]) -> Dict[str, str]:
    """A split's classes, `OLD.Class` -> the new path below tests/ (its line's)."""
    return {name: rel for name, places in MV.formerly_map(head / "tests").items()
            if "." in name and name.split(".")[0] in splits for rel, cls in places if cls}


def without_files(doc: dict, files) -> dict:
    """The baseline DOC with no count for FILES (a split's module, its files)."""
    out = dict(doc)
    for kind in MT.BASELINE_KINDS:
        sect = doc.get(kind)
        if isinstance(sect, dict):
            sect = {code: {k: v for k, v in per.items() if k not in files}
                    if isinstance(per, dict) else per for code, per in sect.items()}
            out[kind] = {code: per for code, per in sect.items() if per != {}}
    return out


def split_plans(base: Path, head: Path, splits: Dict[str, List[str]]
                ) -> Tuple[Dict[str, "MT.SplitPlan"], List[str]]:
    """BASE's mover's plan of each split (MT is BASE's copy), from the
    placement HEAD's class lines make (`Formerly: OLD.Class` on a class of
    NEW: `OLD::Class NEW`), refused as the mover refuses it (SplitPlan,
    run_file_problems): (the plans, the problems)."""
    fmap = MV.formerly_map(head / "tests")
    plans: Dict[str, "MT.SplitPlan"] = {}
    out: List[str] = []
    for old in splits:
        places = []
        for name, where in sorted(fmap.items()):
            if name.count(".") != 1 or name.split(".")[0] != old:
                continue
            lines = sorted(rel for rel, cls in where if cls)
            if len(lines) > 1:
                out.append(f"text: {name} is named by {len(lines)} class lines ({', '.join(lines)}): "
                           f"a class moves to one place")
            places += [(name.split(".")[1], rel) for rel in lines[:1]]
        try:
            plan = MT.SplitPlan(base, old, places)
        except SystemExit as e:
            out += [f"text: BASE's mover refuses the split HEAD's class lines make: "
                    f"{ln.replace('move_tests: ', '', 1)}" for ln in str(e).splitlines()]
            continue
        bad = MT.run_file_problems(base, old, plan.placed)
        if bad:
            out += [f"text: BASE's mover refuses the split of tests/{old}.py: {b}" for b in bad]
            continue
        plans[old] = plan
    return plans, out


def fragment_problems(plan: "MT.SplitPlan", f: str, text: str) -> Tuple[List[str], bool]:
    """A split's new file F (its TEXT) against BASE's plan of the split:
    the plan's text, byte for byte (no problem) or but for its docstrings
    (listed: True)."""
    want = plan.texts.get(f[len("tests/"):])
    if want is None:
        return [f"text: {f} names {plan.old} and takes none of its classes"], False
    if want == text:
        return [], False
    try:
        same = without_docstrings(want) == without_docstrings(text)
    except SyntaxError:
        same = False
    if same:
        return [], True
    n = next((i for i, (x, y) in enumerate(zip(MT.split_lines(want), MT.split_lines(text)), 1)
              if x != y), min(len(MT.split_lines(want)), len(MT.split_lines(text))) + 1)
    return [f"text: {f}: not BASE's mover's cut of tests/{plan.old}.py: line {n} differs "
            f"(docstrings apart)"], False


def held_names(plans: Dict[str, "MT.SplitPlan"]) -> Dict[str, Dict[str, List[str]]]:
    """For each split module, each module-level def or class name -> the
    dotted new files that hold it (a placed class's one, a helper's each
    copy), as BASE's plan cuts it."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for old, plan in plans.items():
        for new, keep in plan.keeps.items():
            for i in keep:
                name = getattr(plan.lay.stmts[i], "name", None)
                if isinstance(name, str):
                    out.setdefault(old, {}).setdefault(name, []).append(MT.dotted(new))
    return out


def split_file_problems(f: str, st: str, nm: str, read, plan: Optional["MT.SplitPlan"],
                        stems: Dict[str, int]) -> Tuple[List[str], bool]:
    """A split's new file F (git's status ST, mode NM; READ gives its text)
    in the text check: new, a plain file, in a package, its name its own,
    BASE's plan's text (fragment_problems; True: listed; no PLAN: the
    mover refuses the split, said apart)."""
    if st != "A":
        return [f"text: {f} is in BASE: a split writes new files"], False
    if nm != "100644":
        return [f"text: {f}: a split's new file, not a plain one (mode {nm})"], False
    out = []
    place = MT.placement_problem(f[len("tests/"):])
    if place:
        out.append(f"text: {f}: {place}")
    if stems.get(Path(f).stem, 0) > 1:
        out.append(f"text: {f}: another module below tests/ is named {Path(f).stem}")
    try:
        text = read()
    except UnicodeDecodeError:
        return out + [f"text: {f}: not text"], False
    if plan is None:
        return out, False
    bad, hand = fragment_problems(plan, f, text)
    return out + bad, hand


def split_problems(head: Path, splits: Dict[str, List[str]],
                   by_path: Optional[dict] = None) -> List[str]:
    """Across a split's files: its module gone (BY_PATH, the batch's
    changes); each file names it once (its docstring's line) and no other
    (its classes' lines name their own class of it). The rest is in BASE's
    plan, which each file's text must be (fragment_problems)."""
    out: List[str] = []
    for old, rels in splits.items():
        gone = (by_path or {}).get(f"tests/{old}.py")
        if by_path is not None and (gone is None or gone[0] != "D"):
            out.append(f"text: tests/{old}.py is still there: a split module goes")
        for rel in rels:
            f = f"tests/{rel}"
            try:
                tree = ast.parse(MT.read_exact(head / f))
            except (ValueError, SyntaxError):
                continue                 # fragment_problems says why
            lines = MV._formerly(ast.get_docstring(tree, clean=True))
            if lines != [old]:
                out.append(f"text: {f}: its docstring names {lines or 'no module'}: a split's file "
                           f"names its module, {old}, once")
            for c in tree.body:
                if isinstance(c, ast.ClassDef):
                    bad = [x for x in MT.class_lines(c) if x != f"{old}.{c.name}"]
                    if bad:
                        out.append(f"text: {f}: {c.name}'s line names {bad[0]}, not "
                                   f"{old}.{c.name}: a file takes classes of one module")
    return out


def _sources(mods: Dict[str, str]) -> Dict[str, List[str]]:
    """New dotted module -> the old modules that name it (one, but for a
    batch that deletes a module under a second `Formerly:` line)."""
    out: Dict[str, List[str]] = {}
    for old, new in mods.items():
        out.setdefault(new, []).append(old)
    return {k: sorted(v) for k, v in out.items()}


def rename_id(tid: str, mods: Dict[str, str], classes: Dict[str, str]) -> str:
    parts = tid.split(".")
    if ".".join(parts[:2]) in classes:
        return ".".join([classes[".".join(parts[:2])]] + parts[1:])
    if parts[0] in mods:
        return ".".join([mods[parts[0]]] + parts[1:])
    return tid


def without_docstrings(text: str) -> str:
    """TEXT with each docstring's literal cut out (its span, by character):
    what is left, comments and a coding line included, must not change."""
    tree = ast.parse(text)
    lines = MT.split_lines(text)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def offset(lineno: int, col: int) -> int:
        # The AST's columns count UTF-8 bytes.
        return starts[lineno - 1] + len(lines[lineno - 1].encode("utf-8")[:col].decode("utf-8"))
    spans = []
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            e = body[0].value
            spans.append((offset(e.lineno, e.col_offset), offset(e.end_lineno, e.end_col_offset)))
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + text[e:]
    return text


def in_tests(f: str) -> bool:
    """F is below a tests/ folder (the root's or a plugin package's)."""
    return f.startswith("tests/") or "/tests/" in f


def is_test_module(f: str) -> bool:
    return f.endswith(".py") and in_tests(f)


def _old_names(olds: Dict[str, str]) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w/.-])(?P<old>" + "|".join(
        re.escape(o) for o in sorted(olds, key=len, reverse=True)) + r")(?P<py>\.py)?(?!\w)")


def settled(want: str, head: str, olds: Dict[str, Union[str, List[str]]]) -> bool:
    """HEAD is WANT with some of its old module names (OLD -> its new path
    below tests/, or a split's paths) put as that move's new name: an old
    name as the new dotted name or path, `OLD.py` as the new path or file
    name. Nothing else changes, and never the other way (TJ-3706,
    TJ-3724)."""
    if not olds:
        return False
    parts, at = [], 0
    for m in _old_names(olds).finditer(want):
        news = olds[m.group("old")]
        forms = []
        for new in ([news] if isinstance(news, str) else news):
            forms += [f"tests/{new}", new] + ([Path(new).name] if m.group("py")
                                              else [MT.dotted(new)])
        parts.append(re.escape(want[at:m.start()]))
        parts.append("(?:" + "|".join(re.escape(x) for x in [m.group(0)] + forms) + ")")
        at = m.end()
    if not parts:
        return False
    parts.append(re.escape(want[at:]))
    return re.fullmatch("".join(parts), head, re.S) is not None


def judge(f: str, want: str, head: str,
          olds: Optional[Dict[str, Union[str, List[str]]]] = None) -> Optional[str]:
    """A changed file against the base's text under the renames: None when
    equal; "listed" when it differs only where a batch writes by hand (a
    test module's docstrings, a .md doc outside any tests/ folder, an old
    name settled to a new one outside src); else the problem."""
    if want == head:
        return None
    olds = olds or {}
    if f.endswith(".md"):
        if not in_tests(f):
            return "listed"
        return f"text: {f}: differs from BASE's beyond the renames (a file below tests/ is data)"
    if is_test_module(f):
        a: Optional[str]
        b: Optional[str]
        try:
            a, b = without_docstrings(want), without_docstrings(head)
        except SyntaxError:
            a = b = None
        if a is not None and b is not None and (a == b or settled(a, b, olds)):
            return "listed"
        return f"text: {f}: differs from BASE's beyond the renames and its docstrings"
    if "src" not in f.split("/")[:-1] and settled(want, head, olds):
        return "listed"
    return f"text: {f}: differs from BASE's beyond the renames"


def init_problem(f: str, text: str) -> Optional[str]:
    """A new package's __init__.py: below tests/rules/ or tests/features/
    (MT.package_dirs_ok), its docstring and nothing else."""
    segs = f.split("/")
    if not (len(segs) >= 3 and segs[0] == "tests" and segs[-1] == "__init__.py"
            and MT.package_dirs_ok(segs[1:-1])):
        return (f"text: {f} is new, not a moved module or a package below tests/rules/ "
                f"or tests/features/")
    try:
        rest = without_docstrings(text)
    except SyntaxError:
        rest = text
    if rest.strip():
        return f"text: {f}: a new package's __init__.py holds more than a docstring"
    return None


_FILE_MODES = ("100644", "100755")


def run_places(base: Path, head: Path, paths: Dict[str, str]
               ) -> Tuple[List[str], Tuple[str, ...], Optional[str]]:
    """Where the run files and the layout test are: BASE's places (BASE
    landed) through the batch's moves (PATHS), never HEAD's own
    `Formerly:` lines, which a batch could drop or rewrite to turn off the
    dotted rename and the ratchet; HEAD's must agree (TJ-3783). (The
    problems, the run files, the layout test: None when BASE has none
    where its lines put it.)"""
    run_files = tuple(paths.get(f, f) for f in MT.run_files(base / "tests"))
    base_layout = MT.layout_path(base / "tests").relative_to(base).as_posix()
    layout = paths.get(base_layout, base_layout)
    if not (base / base_layout).is_file():
        # A commit dropped the moved layout test's line since it moved: no
        # place here can be trusted (TJ-3804; the layout checks fail that
        # commit, as they check each run file is where its lines say).
        return ([f"text: {base_layout}: BASE's `Formerly:` lines put the layout test here, "
                 f"and BASE has no such file"], run_files, None)
    head_places = (*MT.run_files(head / "tests"),
                   MT.layout_path(head / "tests").relative_to(head).as_posix())
    return ([f"text: {want}: BASE and the batch's moves put it here; HEAD's `Formerly:` "
             f"lines, at {there}" for want, there in zip((*run_files, layout), head_places)
             if want != there], run_files, layout)


def _places_said(places: List[Tuple[str, str]]) -> str:
    return ", ".join(f"{rel} ({cls})" if cls else rel for rel, cls in places) or "nowhere"


def earlier_move_problems(base: Path, bmap: Dict[str, List[Tuple[str, str]]],
                          hmap: Dict[str, List[Tuple[str, str]]],
                          paths: Dict[str, str]) -> List[str]:
    """An earlier move's names: every `Formerly:` line naming a module BASE
    has not at tests/ (an earlier batch's whole move or split, each of
    whose files names its module, MV.split_modules) is where BASE (BMAP)
    has it in HEAD's lines too (HMAP), through this batch's moves (PATHS;
    the mover moves only a module at tests/, never a moved one's file): a
    batch adds no file claiming one, drops none, moves none, rewrites or
    adds no line of one (TJ-4210, TJ-4211). This batch's own moves name
    modules BASE has at tests/."""
    def module(n: str) -> str:
        return n.split(".")[0]

    def moved(rel: str) -> str:
        return paths.get(f"tests/{rel}", f"tests/{rel}")[len("tests/"):]
    want: Dict[str, Dict[str, List[Tuple[str, str]]]] = {}
    got: Dict[str, Dict[str, List[Tuple[str, str]]]] = {}
    for fmap, into, place in ((bmap, want, moved), (hmap, got, lambda rel: rel)):
        for n, places in fmap.items():
            if not (base / "tests" / f"{module(n)}.py").is_file():
                into.setdefault(module(n), {})[n] = sorted((place(r), c) for r, c in places)
    split = MV.split_modules(bmap)
    out = []
    for x in sorted(set(want) | set(got)):
        w, g = want.get(x, {}), got.get(x, {})
        bad = [f"{n}: {_places_said(w.get(n, []))} at BASE, {_places_said(g.get(n, []))} after"
               for n in sorted(set(w) | set(g)) if w.get(n) != g.get(n)]
        if bad:
            how = (f"an earlier batch {'split' if x in split else 'moved'}" if x in want
                   else "no batch moved")
            out.append(f"text: {x}, which {how}, is named otherwise than at "
                       f"BASE ({'; '.join(bad)}): a batch's `Formerly:` lines name its own "
                       f"moves, and leave an earlier one's as they are")
    return out


def reverse_problems(base: Path, head: Path, splits: Dict[str, List[str]],
                     paths: Dict[str, str]) -> List[str]:
    """HEAD's `Formerly:` lines against the batch's moves (SPLITS, PATHS):
    batch_moves keeps one place, so a second would hide a rename the batch
    undid (TJ-3751); an old name an earlier batch moved too, whose module
    moved_path finds by its `Formerly:` line (TJ-3768). BASE's own suite
    holds no name twice, but an earlier batch's split, each of whose files
    names its module (TJ-4210); its names, as every earlier move's, stay
    where BASE has them (earlier_move_problems, TJ-4211)."""
    bmap, hmap = MV.formerly_map(base / "tests"), MV.formerly_map(head / "tests")
    problems = earlier_move_problems(base, bmap, hmap, paths)
    earlier = set(MV.split_modules(bmap))
    for old, places in sorted(hmap.items()):
        whole = sorted(rel for rel, cls in places if not cls)    # lines, as moved_path counts
        if "." not in old and len(whole) > 1 and old not in splits and old not in earlier:
            problems.append(f"text: {old} is named whole by {len(whole)} `Formerly:` "
                            f"lines ({', '.join(whole)}): a module moves to one place")
    return problems


def text_problems(repo: Path, base_rev: str, head_rev: str, base: Path, head: Path,
                  mods: Dict[str, str]) -> Tuple[List[str], List[str], int]:
    """Check 2 over what changed from BASE_REV to HEAD_REV, read from git
    (their exports at BASE and HEAD for the mover's counts): (the problems,
    the files that differ only where a batch writes by hand, the number
    changed)."""
    problems: List[str] = []
    olds = {o: d.replace(".", "/") + ".py" for o, d in mods.items()}
    paths = {f"tests/{o}.py": f"tests/{n}" for o, n in olds.items()}
    moved_new = {v: k for k, v in paths.items()}
    splits = split_moves(base, head)
    split_old = {f"tests/{o}.py": o for o in splits}
    split_new = {f"tests/{rel}": o for o, rels in splits.items() for rel in rels}
    plans, refused = split_plans(base, head, splits)
    problems += refused
    ch = changes(repo, base_rev, head_rev)
    by_path = {c[1]: c for c in ch}
    for to_mod, from_mods in sorted(_sources(mods).items()):
        if len(from_mods) > 1:
            problems.append(f"text: tests/{to_mod.replace('.', '/')}.py names {len(from_mods)} "
                            f"old modules ({', '.join(from_mods)}): a module moves from one")
    problems += reverse_problems(base, head, splits, paths)
    bad, run_files, layout = run_places(base, head, paths)
    problems += bad
    if layout is None:
        return problems, [], len(ch)
    listed: List[str] = []
    stems: Dict[str, int] = {}
    for q in [*(head / "tests").rglob("test_*.py"), *head.glob("packages/*/tests/**/test_*.py")]:
        stems[q.stem] = stems.get(q.stem, 0) + 1

    def expected(f: str, base_text: str) -> str:
        """F's text under the mover's renames (the ratchet's count set to the
        moved tree's); the baseline is compared re-keyed, apart."""
        want: str = MT.rename_text(f, base_text, olds, run_files,
                                   split_class_paths(head, splits))
        if f == layout:
            want = re.sub(r"^ROUND_NAMED_MAX = \d+$",
                          f"ROUND_NAMED_MAX = {MT.round_named_count(head / 'tests', head / layout)}",
                          want, count=1, flags=re.M)
        return want

    def text_of(blob: str) -> str:
        return _git(repo, "cat-file", "blob", blob).decode("utf-8")
    problems += [x for c in ch if c[1] == UNSTABLE
                 for x in unstable_text_problems(c[0], c[3], lambda: text_of(c[5]), listed)]
    for st, f, om, nm, ob, nb in [c for c in ch if c[1] != UNSTABLE]:
        if f in TOOLS:
            problems.append(f"text: {f} differs from BASE's: the tools change in a "
                            f"commit of their own")
            continue
        if {om, nm} & {"120000", "160000"}:
            problems.append(f"text: {f}: a symlink or a submodule ({om} -> {nm})")
            continue
        if st == "D":
            if f not in paths and f not in split_old:
                problems.append(f"text: {f} deleted, not moved")
            continue
        if f in split_new:
            bad, hand = split_file_problems(f, st, nm, lambda: text_of(nb),
                                            plans.get(split_new[f]), stems)
            problems += bad
            listed += [f] if hand else []
            continue
        if f in moved_new:
            old_f = moved_new[f]
            gone = by_path.get(old_f)
            if gone is None or gone[0] != "D":
                problems.append(f"text: {old_f} is still there: a module moves, never copies")
                continue
            if st != "A":
                problems.append(f"text: {f} is in BASE: a module moves to a new path")
                continue
            om, ob = gone[2], gone[4]
            place = MT.placement_problem(f[len("tests/"):])
            if place:
                problems.append(f"text: {f}: {place}")
            if stems.get(Path(f).stem, 0) > 1:
                problems.append(f"text: {f}: another module below tests/ is named "
                                f"{Path(f).stem}")
        if om != "000000" and om != nm:
            problems.append(f"text: {f}: its mode changed ({om} -> {nm})")
            continue
        if nm not in _FILE_MODES:
            problems.append(f"text: {f}: not a plain file (mode {nm})")
            continue
        try:
            head_text = text_of(nb)
            base_text = None if st == "A" and f not in moved_new else text_of(ob)
        except UnicodeDecodeError:
            problems.append(f"text: {f}: not text, and changed")
            continue
        if base_text is None:
            bad = init_problem(f, head_text) if nm == "100644" else \
                f"text: {f} is new and executable"
            if bad:
                problems.append(bad)
            continue
        if f in moved_new:
            try:
                base_text = MT.add_formerly(base_text, Path(moved_new[f]).stem)
            except (ValueError, SyntaxError, AssertionError) as e:
                problems.append(f"text: {f}: the mover could not have moved it ({e})")
                continue
        want = expected(f, base_text)
        if f == str(MT.BASELINE):
            # The mover re-keys the per-file counts; nothing else may change.
            if without_files(MT.rekey_baseline(json.loads(base_text), paths), split_old) != \
                    without_files(json.loads(head_text), split_new):
                problems.append(f"text: {f} differs from BASE's beyond the re-keying")
            continue
        verdict = judge(f, want, head_text, {**olds, **splits})
        if verdict == "listed":
            listed.append(f)
        elif verdict:
            problems.append(verdict)
    problems += split_problems(head, splits, by_path)
    # A file the batch left alone needed no rename (TJ-3732).
    word = re.compile("|".join(re.escape(o) for o in [*olds, *splits])) if olds or splits \
        else None
    for q in sorted(head.rglob("*")):
        f = q.relative_to(head).as_posix()
        if f in by_path or f in TOOLS or f in MT.HISTORY or not q.is_file() or q.is_symlink():
            continue
        special = f in (layout, str(MT.BASELINE))
        if not special and f.startswith(MT.SKIP_PREFIXES):
            continue
        try:
            text = q.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not special and not (word and word.search(text)):
            continue
        if f == str(MT.BASELINE):
            doc = json.loads(text)
            if MT.rekey_baseline(json.loads(text), paths) != doc or \
                    without_files(doc, split_old) != doc:
                problems.append(f"text: {f} is BASE's: its counts for a moved module are not "
                                f"re-keyed")
        elif expected(f, text) != text:
            problems.append(f"text: {f} is BASE's, but the moves rename in it")
    return problems, listed, len(ch)


def _stage_sets(tree: Path) -> Tuple[set, set]:
    """The no-extras stage's modules (its own --list) and the release-edits
    stage's (its grep, as ci.sh runs it)."""
    nx = subprocess.run(["bash", str(tree / "scripts" / "ci_no_extras.sh"), "--list"],
                        capture_output=True, text=True, check=True).stdout.split()
    ci = (tree / "scripts" / "ci.sh").read_text(encoding="utf-8")
    rel = re.search(r"\$\((grep -rlE --include='test_\*\.py' .*?\| sort)\)", ci, re.S).group(1)
    out = subprocess.run(["bash", "-c", rel], cwd=tree, capture_output=True,
                         text=True, check=True).stdout.split()
    return set(nx), set(out)


def wiring_problems(base_sets: Tuple[set, set], head_sets: Tuple[set, set],
                    mods: Dict[str, str], splits: Optional[Dict[str, List[str]]] = None,
                    classes: Optional[Dict[str, str]] = None,
                    picks: Optional[Dict[str, Set[str]]] = None) -> List[str]:
    """The stages' selections (_stage_sets) in HEAD are BASE's, renamed (a
    split's class, `OLD.Class`, through its line); for a split module a
    stage selected, the new files of it PICKS (stage -> the split's new
    files, dotted, whose text the stage's grep picks: stage_picks) has,
    and no other (one that leaves the stage: stage_leavers)."""
    splits, classes, picks = splits or {}, classes or {}, picks or {}
    out = []
    for name, b, h in zip(("no-extras", "release-edits"), base_sets, head_sets):
        want = {rename_id(x, mods, classes) for x in b if x not in splits}
        want |= {MT.dotted(r) for x in b if x in splits for r in splits[x]
                 if MT.dotted(r) in picks.get(name, set())}
        if want != h:
            out.append(f"wiring: the {name} stage selects {sorted(h ^ want)} differently")
    return out


def stage_picks(base: Path, head: Path, splits: Dict[str, List[str]]) -> Dict[str, Set[str]]:
    """Stage -> the splits' new files (dotted) whose HEAD text the stage's
    grep picks (BASE's patterns: MT.stage_patterns, MT.stage_selects)."""
    out: Dict[str, Set[str]] = {}
    for name, pattern in MT.stage_patterns(base).items():
        out[name] = {MT.dotted(r) for rels in splits.values() for r in rels
                     if MT.stage_selects(pattern, MT.read_exact(head / "tests" / r))}
    return out


def stage_leavers(base_sets: Tuple[set, set], head_sets: Tuple[set, set],
                  splits: Dict[str, List[str]]) -> List[str]:
    """The splits' new files that leave a stage which selected their module
    (their tests run in the suite still), as `stage: module`."""
    return [f"{name}: {MT.dotted(r)}"
            for name, b, h in zip(("no-extras", "release-edits"), base_sets, head_sets)
            for x in sorted(b) if x in splits for r in splits[x] if MT.dotted(r) not in h]


def pin_keys(key: str, mods: Dict[str, str], classes: Dict[str, str],
             held: Dict[str, Dict[str, List[str]]]) -> List[str]:
    """A BASE pin's key (`module.Class.test`, `module.def`) in HEAD: renamed
    (rename_id), or, for a split module's name no class line takes, the
    same name in each file that holds a copy (a mixin's, a helper's)."""
    parts = key.split(".")
    if parts[0] in held and ".".join(parts[:2]) not in classes:
        return [".".join([d] + parts[1:]) for d in held[parts[0]].get(parts[1], [])]
    return [rename_id(key, mods, classes)]


def split_baseline_problems(base: Path, head: Path, splits: Dict[str, List[str]],
                            plans: Optional[Dict[str, "MT.SplitPlan"]] = None) -> List[str]:
    """HEAD's baseline for a split: no count left for the module; each new
    file's ruff counts at most BASE's plan's bound (SplitPlan.ruff_bound: the
    hits of BASE's module, ruff run on BASE's file, on the lines it took,
    a copy's again, none of its own); its suppression counts its own
    comments' (BASE's check_architecture, noqa_in_text)."""
    if not splits:
        return []
    if plans is None:
        plans, _refused = split_plans(base, head, splits)
    out: List[str] = []
    hb = json.loads((head / MT.BASELINE).read_text(encoding="utf-8"))
    gone = {f"tests/{o}.py" for o in splits}
    if without_files(hb, gone) != hb:
        out.append(f"baseline: HEAD still counts {sorted(gone)}, which the batch split")
    noqa_in_text = None
    ca = base / "scripts" / "check_architecture.py"
    if ca.is_file():
        spec = importlib.util.spec_from_file_location("_verify_ca", ca)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        noqa_in_text = getattr(mod, "noqa_in_text", None)
    if noqa_in_text is None:
        out.append("baseline: BASE's scripts/check_architecture.py has no noqa_in_text to count "
                   "a split's suppressions")
    hits = MT.ruff_hits(base, sorted(gone))
    if hits is None:
        out.append(f"baseline: ruff does not run with {sys.executable}: a split's ruff counts "
                   f"need it (run the proof with the dev venv's python3)")
    for old, plan in plans.items():
        plan.hits = (hits or {}).get(f"tests/{old}.py", [])
        for rel in plan.news:
            path = f"tests/{rel}"
            text = MT.read_exact(head / path)
            got = {code: per[path] for code, per in hb.get("ruff", {}).items() if path in per}
            if hits is not None:
                may = plan.ruff_bound(rel)
                for code in sorted(c for c, n in got.items() if n > may.get(c, 0)):
                    out.append(f"baseline: {path}: ruff {code} {got[code]}, more than the "
                               f"{may.get(code, 0)} hit(s) on the lines of tests/{old}.py it took")
            if noqa_in_text is not None:
                own = noqa_in_text(text)
                gotq = {code: per[path] for code, per in hb.get("noqa", {}).items() if path in per}
                if gotq != own:
                    out.append(f"baseline: {path}: its noqa counts {gotq}, its comments {own}")
    return out


def _seed_dir(td: Path) -> Path:
    """The folder of the children's sitecustomize (_SEED_SITE), made once in TD."""
    d = td / "seed"
    if not (d / "sitecustomize.py").is_file():
        d.mkdir(exist_ok=True)
        (d / "sitecustomize.py").write_text(_SEED_SITE, encoding="utf-8")
    return d


def run_outcomes(tree: Path, names: List[str], td: Path,
                 record: Optional[Dict[str, Union[str, bool]]] = None) -> dict:
    """Each test's outcome (_RECORD) when unittest runs NAMES (dotted)
    in TREE's tests/, in one process: the runner's setup, offline, width
    0, a new empty HOME and TMPDIR, PYTHONHASHSEED 0, the ids run; given
    RECORD (`own`: the module's dotted name, `file`: its path below
    tests/, `alias`: OLD's name after the split, `by_hand`: list OLD's
    unconditional skips), each part's record too (`parts`)."""
    run = Path(tempfile.mkdtemp(prefix="run-", dir=td))
    home, tmp = run / "home", run / "tmp"
    home.mkdir()
    tmp.mkdir()
    out = run / "outcomes.json"
    own = (record or {}).get("own") or names[0].split(".")[0]
    cfg = {"out": str(out), "names": names, "record": record is not None, "tree": str(tree),
           "own": own, "own_file": str(tree / "tests" / record["file"]) if record else "",
           "alias": (record or {}).get("alias", ""), "by_hand": bool((record or {}).get("by_hand")),
           "limit": LIMIT, "depth": DEPTH, "triggers": _TRIGGERS}
    (run / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = _env(tree, str(home))
    env.update(PYTHONHASHSEED="0", TMPDIR=str(tmp),
               PYTHONPATH=os.pathsep.join([str(tree / "src"), str(_seed_dir(td))]))
    r = subprocess.run([sys.executable, "-c", _RECORD, str(run / "config.json")],
                       cwd=tree / "tests", env=env, capture_output=True, text=True)
    if r.returncode or not out.is_file():
        return {"tests": {}, "ids": [], "parts": {},
                "held": [f"the run of {' '.join(names)} failed: "
                         f"{(r.stderr or r.stdout)[-300:].strip()}"]}
    return json.loads(out.read_text(encoding="utf-8"))


def _held(entries: List[str]) -> Set[str]:
    """A fixture's errors without their module (`setUpModule (test_x)` and
    `setUpModule (rules.x.test_y)` alike; a class keeps its name)."""
    return {re.sub(r"\(([\w.]+)\)", lambda m: f"({m.group(1).split('.')[-1]})"
                   if "Class" in e.split(" ", 1)[0] else "", e).replace(" :", ":")
            for e in entries}


def _where(key: str) -> str:
    """A record's line or code key, in words: OLD's line `<own>|Q|OFF|I|N`
    as `Q line +N` (its line in the def; `nested at +OFF` for code inside
    a function), its code `<own>|Q|OFF|I` as `Q`; a helper's line
    `tests/x.py|Q|FIRST|N` as `tests/x.py:LINE (Q)`, its code
    `tests/x.py|Q|FIRST` as `tests/x.py (Q)`."""
    p = key.split("|")
    if p[0] == "<own>" and len(p) in (4, 5):
        at = f" (nested at +{p[2]})" if p[2] not in ("0", "") and not p[2].startswith("@") else ""
        return f"{p[1]}{at}" + (f" line +{p[4]}" if len(p) == 5 else "")
    if p[0] != "<own>" and len(p) == 4 and p[2].isdigit() and p[3].lstrip("-").isdigit():
        return f"{p[0]}:{int(p[2]) + int(p[3])} ({p[1]})"
    if p[0] != "<own>" and len(p) == 3:
        return f"{p[0]} ({p[1]})"
    return key


ABSENT = "<absent>"
# The families of values whose absence a run that reads them shows (an
# environment variable unset, a class attribute gone). A test-tree
# helper's or a product module's name is ABSENT where its module was
# loaded at the part's start (the record's `loaded`; a product module's
# name looked for there, not `unseen`); where the module was not loaded,
# the part imports it fresh and its value is not compared: counted. A name
# of OLD's a run's module lacks is ABSENT where the run's code looked it up
# on the module (`getattr(sys.modules[...], "X", ())`: the record's
# `missing`), or where in a BASE run its value at the part's start is not
# the one OLD's import left (the run's `imported`), or the import did not
# make it: an earlier class changed it. Else (a constant, `"US"` a string
# beside it) not compared: a split takes nothing from it. (A read by name
# fails; one by a string, `globals()`, `sys.modules[name]`, the mover
# refuses: own_namespace.)
_SPECIAL = ("<env>", "<cwd>", "<decimal>", "<class>")


class _Cmp:
    """What one part's comparison finds: the differences (words, kind:
    `loop` or `other`); the parts dropped (key, words: the BASE runs'
    values shown); the bounds an accepted drop still keeps ({key: what
    breaks them}); the digit runs left out of a comparison (words, BASE's
    values and HEAD's); the values not compared, their module not loaded
    ({module: n})."""

    def __init__(self) -> None:
        self.found: List[Tuple[str, str]] = []
        self.dropped: List[Tuple[str, str]] = []
        self.bounds: Dict[str, str] = {}
        self.waived: List[str] = []
        self.unloaded: Dict[str, int] = {}

    def drop(self, key: str, text: str) -> None:
        if all(k != key for k, _t in self.dropped):
            self.dropped.append((key, text))


def _same(vals: list) -> bool:
    return all(v == vals[0] for v in vals[1:])


def _loop_small(b: int, hs: List[int]) -> bool:
    """A loop's count that differs by a few (polling, a deadline)."""
    return b >= 2 and all(x >= 2 and abs(x - b) <= max(2, b // 50) for x in hs)


def _shows(fs: list) -> str:
    """The BASE runs' values as their finer records show them (a short
    repr each, digits and all)."""
    got = [f[3] if f and len(f) > 3 and f[3] is not None else "?" for f in fs]
    if all(x == "?" for x in got):
        return ""
    if "?" not in got and _same(got):
        return f"{got[0]} in each, past what shows"
    return " / ".join(got)


def _num(x: str) -> Optional[float]:
    try:
        return float(x)
    except ValueError:
        return None


def _kinds(x: str) -> str:
    return re.sub(r"[0-9]", "9", re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", x)))


def _run_fits(vb: List[str], v: str) -> Optional[str]:
    """HEAD's digit run V where the BASE runs hold VB, which disagree: a
    number lies within their range widened by its spread (the larger of 2
    and their greatest less their least: HEAD runs between BASE's in time,
    so a clock's, an mtime's, a pid's value does); adjacent runs the BASE
    runs disagree on, a timestamp's fields, may instead lie between theirs
    read together (_between); a run not numeric has the length and the
    kinds of character (digit, lower, upper, other) of one of theirs. None
    where it does, else why not."""
    nb, nv = [_num(x) for x in vb], _num(v)
    if nv is not None and all(x is not None for x in nb):
        lo, hi = min(x for x in nb if x is not None), max(x for x in nb if x is not None)
        w = max(2.0, hi - lo)
        if lo - w <= nv <= hi + w:
            return None
        # The spread exactly: `:g` would print a clock's microseconds
        # over a million as 1.2e+06.
        spread = str(int(w)) if w == int(w) else repr(w)
        return (f"{v} after, outside BASE's {min(vb, key=float)} to {max(vb, key=float)} "
                f"widened by its spread {spread}")
    if any(len(v) == len(x) and _kinds(v) == _kinds(x) for x in vb):
        return None
    return f"{v} after, not the length and characters of BASE's {'/'.join(vb)}"


def _runs_level(bf: list, hf: list) -> Optional[Tuple[Optional[str], Optional[str], List[str]]]:
    """A comparison with the digit runs masked (BF the BASE runs' finer
    records, HF HEAD's): None where the BASE runs disagree even so, or one
    lacks the runs; else (why a HEAD run differs, or None: in the masked
    digest, or in a run the BASE runs agree on; why it is dropped, or
    None: a run they disagree on beyond _run_fits, which a row may accept;
    each run they disagree on, BASE's values and HEAD's)."""
    if any(not f or f[0] is None or f[1] is None for f in bf):
        return None
    if not _same([f[0] for f in bf]) or not _same([len(f[1]) for f in bf]):
        return None
    rb = [f[1] for f in bf]
    loose = {i for i in range(len(rb[0])) if not _same([r[i] for r in rb])}
    if not loose:                 # (a `#` of the text and a run apart: no runs to leave out)
        return None
    if any(not f or f[0] != bf[0][0] or f[1] is None or len(f[1]) != len(rb[0])
           or any(f[1][i] != rb[0][i] for i in range(len(rb[0])) if i not in loose)
           for f in hf):
        return "differs beyond the digits the BASE runs disagree on", None, []
    out, why = [], None
    for i in sorted(loose):
        out.append(f"{'/'.join(r[i] for r in rb)} -> {'/'.join(f[1][i] for f in hf)}")
    for g in _groups(sorted(loose)):
        tb, th = [[r[i] for i in g] for r in rb], [[f[1][i] for i in g] for f in hf]
        if _between(tb, th):
            continue
        bad = next((x for t in th for x in (_run_fits([b[k] for b in tb], t[k])
                                            for k in range(len(g))) if x), None)
        if bad and why is None:
            why = f"its digit run {g[0] + 1}: {bad}" if len(g) == 1 else (
                f"its digit runs {g[0] + 1} to {g[-1] + 1} ({'/'.join(th[0])} after, not "
                f"between BASE's {'/'.join(min(tb, key=_key))} and "
                f"{'/'.join(max(tb, key=_key))}): {bad}")
    return None, why, out


def _groups(ix: List[int]) -> List[List[int]]:
    """Runs of consecutive indices (a timestamp's minute and second)."""
    out: List[List[int]] = []
    for i in ix:
        if out and out[-1][-1] == i - 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _key(t: List[str]) -> List[float]:
    return [_num(x) or 0.0 for x in t]


def _between(tb: List[List[str]], th: List[List[str]]) -> bool:
    """Adjacent digit runs (a timestamp's fields, read together: its
    second wraps as its minute turns) of each HEAD run between the BASE
    runs' least and greatest, in order; numbers only, two runs or more,
    each after the first a fixed-width time field (one length and kinds
    of character in every run, two integer digits at most: `04`,
    `47.679421`), so a later field holds no more than a clock's."""
    if len(tb[0]) < 2 or any(_num(x) is None for t in tb + th for x in t):
        return False
    for k in range(1, len(tb[0])):
        col = [t[k] for t in tb + th]
        if not _same([(len(x), _kinds(x)) for x in col]) or len(col[0].split(".")[0]) > 2:
            return False
    lo, hi = min(map(_key, tb)), max(map(_key, tb))
    return all(lo <= _key(t) <= hi for t in th)


def _which(bd: list, hd: list) -> Tuple[int, str]:
    """How many BASE runs hold the HEAD value fewest of them hold, and for
    each HEAD run the BASE runs that hold its value (`1+2 / 3`)."""
    least = min(sum(1 for d in bd if d == h) for h in hd)
    return least, " / ".join("+".join(str(i + 1) for i, d in enumerate(bd) if d == h) for h in hd)


def _matches(c: _Cmp, key: str, said: str, bd: list, hd: list, sb: str, sh: str) -> None:
    """Each HEAD value (HD) one of the BASE runs' (BD, which disagree; SB
    and SH their values shown): some BASE run lacks it, so the part is
    dropped (a row accepts it, saying why the value is flaky at BASE)."""
    least, which = _which(bd, hd)
    shown = f"at BASE {sb}, after {sh}" if sb or sh else "the values do not show"
    held = f"only {least}" if least * 2 < len(bd) else f"{least}, not all,"
    c.drop(key, f"{said}, after what {held} of the {len(bd)} BASE runs hold (BASE run {which}; "
                f"{shown})")


def _compare(c: _Cmp, key: str, said: str, told: str, bd: list, bf: list, hd: list,
             hf: list) -> None:
    """One part in each BASE run (BD its digests, BF its finer records) and
    each HEAD run (HD, HF). Where the BASE runs agree, a HEAD run that
    differs is a difference (TOLD). Where they disagree: each HEAD run one
    BASE run's, _matches (dropped: some BASE run lacks it); else compared
    with the digit runs masked (the runs they agree on compared, the
    others within their range widened by its spread, listed, or dropped:
    _runs_level), else
    piece by piece (a dict's keys, a sequence's elements, a text's lines,
    an object's attributes), each the same way; what is left is dropped
    (KEY), the BASE runs' values shown. No HEAD run (none agrees on an
    item's coarse parts, a difference found already): nothing more."""
    if not hd:
        return
    if _same(bd):
        if any(d != bd[0] for d in hd):
            c.found.append((told, "other"))
        return
    if all(d in bd for d in hd):
        _matches(c, key, said, bd, hd, _shows(bf), _shows(hf))
        return
    lv = _runs_level(bf, hf)
    if lv is not None:
        if lv[0]:
            c.found.append((f"{said} {lv[0]}", "other"))
        elif lv[1]:
            c.drop(key, f"{said}, {lv[1]}")
        else:
            c.waived.append(f"`{key}` ({', '.join(lv[2])})")
        return
    if bf and all(f and f[2] is not None for f in bf):
        if not all(f and f[2] is not None for f in hf):
            c.found.append((f"{said} differs: it has no pieces to compare after", "other"))
            return
        pb = [{x[0]: x[1:] for x in f[2]} for f in bf]
        ph = [{x[0]: x[1:] for x in f[2]} for f in hf]

        def fin(x: Optional[list]) -> Optional[list]:
            return [x[1], x[2], None, x[3]] if x else None
        for n in sorted(set().union(*pb, *ph)):
            xb, xh = [x.get(n) for x in pb], [x.get(n) for x in ph]
            _compare(c, f"{key} {n}", f"{said} {n}", f"{said} {n} differs",
                     [x[0] if x else ABSENT for x in xb], [fin(x) for x in xb],
                     [x[0] if x else ABSENT for x in xh], [fin(x) for x in xh])
        return
    shown = _shows(bf)
    c.drop(key, f"{said}, at BASE {shown}" if shown else said)


def _fine_said(key: str) -> str:
    kind, _, rest = key.partition(" ")
    if kind == "arg":
        return f"has another argument {rest}" if rest.isdigit() else f"has another {rest}="
    if kind == "cwd":
        return "runs in another folder"
    if kind == "env":
        return f"runs with another {rest} in its environment"
    stream = "prints" if kind == "stdout" else "writes to stderr"
    return f"{stream} another number of lines" if rest == "lines" else f"{stream} another {rest}"


def _assert_fine(x: list) -> Dict[str, str]:
    return {f"arg {a[0]}": a[1] for a in x[2]}


def _assert_finer(x: list) -> Dict[str, Optional[list]]:
    return {f"arg {a[0]}": a[2] if len(a) > 2 else None for a in x[2]}


def _child_fine(x: list) -> Dict[str, str]:
    out = {"cwd": x[2], **{f"env {k}": d for k, d in x[3].items()}}
    for name, lines in (("stdout", x[4]), ("stderr", x[5])):
        if lines is not None:
            out[f"{name} lines"] = str(len(lines))
            out.update({f"{name} line {i}": d for i, d in enumerate(lines, 1)})
    return out


def _child_finer(x: list) -> Dict[str, Optional[list]]:
    out: Dict[str, Optional[list]] = {"cwd": [None, None, None, x[2]]}
    for name, lines, fs in (("stdout", x[4], x[6] if len(x) > 6 else None),
                            ("stderr", x[5], x[7] if len(x) > 7 else None)):
        if lines is not None:
            out[f"{name} lines"] = [None, None, None, str(len(lines))]
            out.update({f"{name} line {i}": f for i, f in enumerate(fs or (), 1)})
    return out


def _items(c: _Cmp, name: str, told: str, xb: list, xh: list, fine, finer) -> None:
    """One item (an assertion, a child) in each BASE run and each HEAD run
    that agrees on the coarse parts: its fine parts one by one (_compare)."""
    fb, fh = [fine(x) for x in xb], [fine(x) for x in xh]
    gb, gh = [finer(x) for x in xb], [finer(x) for x in xh]
    for key in sorted(set().union(*fb, *fh)):
        _compare(c, f"{name} {key}", f"{name}'s {key}", f"{told} {_fine_said(key)}",
                 [f.get(key, ABSENT) for f in fb], [g.get(key) for g in gb],
                 [f.get(key, ABSENT) for f in fh], [g.get(key) for g in gh])


def _site_items(what: str, s: list) -> str:
    """A site's items in one run, in words: their number, their methods
    (an assertion's) or exits (a child's)."""
    kinds = sorted({x[0] if what == "assertion" else f"exit {x[1]}" for x in s})
    return f"{len(s)} ({', '.join(kinds)})" if s else "0"


def _sequence(c: _Cmp, what: str, plural: str, bs: List[list], hs: List[list], said, fine,
              finer, group, loops: Set[str]) -> None:
    """A sequence part (assertions, children): each item's coarse part (a
    method and its site; an argv and its returncode) in order, then its
    fine parts one by one (an argument; the folder, a variable, an output
    line). A HEAD run that differs where the BASE runs agree is a
    difference; a difference in the order that only items at a loop's
    lines (LOOPS) make is marked so. Where the BASE runs disagree on the
    coarse parts, the items are compared site by site (GROUP: an
    assertion's site, a child's argv): a site whose items the BASE runs
    disagree on is dropped, by itself; the rest are compared."""
    cb = [[json.dumps(x[:2]) for x in s] for s in bs]
    if not _same(cb):
        for g in sorted({group(x) for s in bs + hs for x in s}):
            ib = [[x for x in s if group(x) == g] for s in bs]
            ih = [[x for x in s if group(x) == g] for s in hs]
            gc = [[json.dumps(x[:2]) for x in s] for s in ib]
            where = _where(g) if what == "assertion" else f"`{g}`"
            if not _same(gc):
                c.drop(f"{plural} at {g}" if what == "assertion" else f"{plural} {g}",
                       f"the {plural} at {where}, which and how many: at BASE "
                       + " / ".join(_site_items(what, s) for s in ib))
                continue
            agree = [s for s in ih if [json.dumps(x[:2]) for x in s] == gc[0]]
            if len(agree) < len(ih):
                s = next(s for s in ih if [json.dumps(x[:2]) for x in s] != gc[0])
                c.found.append((f"the {plural} at {where} are others after: at BASE "
                                f"{_site_items(what, ib[0])}, after {_site_items(what, s)}",
                                "other"))
            for j in range(len(gc[0])):
                x = ib[0][j]
                _items(c, f"{what} at {g} #{j + 1}", f"{what} {j + 1} of {len(gc[0])} at {where} ("
                       + (x[0] if what == "assertion" else f"exit {x[1]}") + ")",
                       [s[j] for s in ib], [s[j] for s in agree], fine, finer)
        return
    agree = []
    told = False
    for s in hs:
        ch = [json.dumps(x[:2]) for x in s]
        if ch == cb[0]:
            agree.append(s)
            continue
        if told:
            continue
        told = True
        i = next((i for i, (x, y) in enumerate(zip(cb[0], ch)) if x != y), min(len(cb[0]), len(ch)))
        if i < len(cb[0]):
            text = (f"{what} #{i + 1} at BASE, {said(bs[0][i])}, "
                    + (f"is {said(s[i])} after" if i < len(s) else "is not made after"))
        else:
            text = f"{what} #{i + 1} after, {said(s[i])}, is not made at BASE"
        rest_b = [k for k, x in zip(cb[0], bs[0]) if x[1] not in loops]
        rest_h = [k for k, x in zip(ch, s) if x[1] not in loops]
        c.found.append((text, "loop" if loops and rest_b == rest_h else "other"))
    for i in range(len(cb[0])):
        _items(c, f"{what} #{i + 1}", f"{what} #{i + 1} ({said(bs[0][i])})",
               [s[i] for s in bs], [s[i] for s in agree], fine, finer)


def _vname(k: str) -> str:
    fam, _, rest = k.partition("|")
    if fam == "<env>":
        return f"the environment's {rest}"
    if fam == "<cwd>":
        return "the working folder"
    if fam == "<decimal>":
        return "the decimal context" + ("'s flags" if rest else "")
    if fam == "<class>":
        return f"the class attribute {rest}"
    if fam == "<prod>":
        return rest.replace("|", ".")
    return rest if fam == "<own>" else f"{fam}.{rest}"


def _return_name(k: str) -> str:
    code, kind, name = k.rsplit("|", 2)
    if name == "<value>":
        return f"{_where(code)}'s {'return' if kind == 'r' else 'yielded'} value"
    return f"{_where(code)}'s local {name}" + (" at its yields" if kind == "y" else "")


def _since_import(b: dict, k: str) -> bool:
    """OLD's name K in BASE record B holds at the part's start what OLD's
    import did not leave there (another value, or none made)."""
    v = b["values"].get(k)
    return v is not None and b.get("imported", {}).get(k.partition("|")[2]) != v


def _value_at(r: dict, names: Set[str], k: str,
              base: Optional[List[dict]] = None) -> Tuple[Optional[str], Optional[str]]:
    """Value K in run R (NAMES what its code names): (its digest, ABSENT,
    or None where not compared; the module not loaded, if that is why).
    A name of OLD's R's module lacks: ABSENT where R's code looked it up
    there, or a BASE run's (BASE) changed it since OLD's import; else not
    compared."""
    v = r["values"].get(k)
    fam, _, rest = k.partition("|")
    if v is None and fam == "<own>" and rest in r.get("missing", ()):
        return ABSENT, None
    if v is not None or not value_read(k, names, _TRIG):
        return v, None
    if fam in _SPECIAL:
        return ABSENT, None
    loaded = r.get("loaded")
    if loaded is None:                    # a record that does not say
        return None, None
    if fam == "<own>":
        return (ABSENT if base and any(_since_import(b, k) for b in base) else None), None
    mod, _, name = rest.partition("|") if fam == "<prod>" else (fam, "", rest)
    if mod not in loaded or fam == "<prod>" and name.split(".")[0] in r.get("unseen", ()):
        return None, mod
    return ABSENT, None


def _lines_part(c: _Cmp, bs: List[dict], hs: List[dict], loops: Set[str]) -> None:
    """Each test-tree line's count. One the BASE runs disagree on is
    dropped, its counts shown, unless each HEAD run's is one of theirs;
    accepted, it still stays nonzero where every BASE run's is, and within
    their range widened by 2 or a tenth, whichever is more (bounds)."""
    for k in sorted(set().union(*(r["lines"] for r in bs + hs))):
        vb = [r["lines"].get(k, 0) for r in bs]
        vh = [r["lines"].get(k, 0) for r in hs]
        if _same(vb):
            if any(v != vb[0] for v in vh):
                small = _loop_small(vb[0], vh)
                loops |= {k} if small else set()
                c.found.append((f"{_where(k)} ran {vb[0]} time(s) at BASE, "
                                f"{'/'.join(map(str, sorted(set(vh))))} after",
                                "loop" if small else "other"))
            continue
        if all(v in vb for v in vh):
            _matches(c, f"line {k}", f"the count of {_where(k)}", vb, vh,
                     "/".join(map(str, vb)), "/".join(map(str, vh)))
            continue
        c.drop(f"line {k}", f"the count of {_where(k)}, at BASE {'/'.join(map(str, vb))}")
        lo, hi = min(vb), max(vb)
        w = max(2, -(-hi // 10))
        bad = sorted({v for v in vh if (v == 0 and lo > 0) or not lo - w <= v <= hi + w})
        if bad:
            c.bounds[f"line {k}"] = (
                f"{_where(k)} ran {'/'.join(map(str, bad))} time(s) after, {lo} to {hi} at BASE "
                f"(an accepted count stays nonzero where BASE's is, and within BASE's range "
                f"widened by {w})")


def _values_part(c: _Cmp, bs: List[dict], hs: List[dict]) -> None:
    """Each value read at the start (value_read), by _compare."""
    nb = [set(r.get("names", ())) for r in bs]
    nh = [set(r.get("names", ())) for r in hs]
    for k in sorted(set().union(*(r["values"] for r in bs + hs))):
        vb = [_value_at(r, n, k)[0] for r, n in zip(bs, nb)]
        if all(v is None for v in vb):
            continue
        got = [(_value_at(r, n, k, bs), r) for r, n in zip(hs, nh)]
        mods = {m for (v, m), _r in got if v is None and m is not None}
        for m in mods:
            c.unloaded[m] = c.unloaded.get(m, 0) + 1
        vh = [(v, r.get("fine", {}).get(k)) for (v, _m), r in got if v is not None]
        other = next((v for v, _f in vh if v != vb[0]), None)
        told = f"{_vname(k)} " + ("is unset" if other == ABSENT else "is set" if vb[0] == ABSENT
                                  else "holds another value") + " at its start"
        _compare(c, f"value {k}", f"the value of {_vname(k)}", told, vb,
                 [r.get("fine", {}).get(k) for r in bs], [v for v, _f in vh], [f for _v, f in vh])


def _calls_shown(fs: list) -> str:
    """Each run's calls (their finer records FS) as their short reprs show
    them; empty where none shows."""
    got = [", ".join(str(x[4]) if len(x) > 4 and x[4] is not None else "?" for x in f) if f
           else "?" for f in fs]
    return "" if all(x.strip("?, ") == "" for x in got) else " / ".join(got)


def _returns_part(c: _Cmp, bs: List[dict], hs: List[dict]) -> None:
    """Each function's return value and each local: a multiset over its
    calls; one the BASE runs disagree on is compared call by call (four
    calls at most) by _compare."""
    for k in sorted(set().union(*(r["returns"] for r in bs + hs))):
        vb = [r["returns"].get(k, {}) for r in bs]
        vh = [r["returns"].get(k, {}) for r in hs]
        said = _return_name(k)
        if _same(vb):
            if any(v != vb[0] for v in vh):
                c.found.append((f"{said} differs", "other"))
            continue
        fb = [r.get("rfine", {}).get(k) for r in bs]
        fh = [r.get("rfine", {}).get(k) for r in hs]
        if all(v in vb for v in vh):
            _matches(c, f"local {k}", said, [json.dumps(v, sort_keys=True) for v in vb],
                     [json.dumps(v, sort_keys=True) for v in vh], _calls_shown(fb),
                     _calls_shown(fh))
            continue
        if all(vb) and not all(vh):
            c.found.append((f"{said} is not reached after", "other"))
            continue
        if not all(f for f in fb + fh) or not _same([len(f) for f in fb]):
            c.drop(f"local {k}", said)
            continue
        if any(len(f) != len(fb[0]) for f in fh):
            c.found.append((f"{said} differs: {len(fb[0])} call(s) at BASE, "
                            f"{'/'.join(str(len(f)) for f in fh)} after", "other"))
            continue
        for i in range(len(fb[0])):
            at = f" call {i + 1}" if len(fb[0]) > 1 else ""
            _compare(c, f"local {k}{at}", f"{said}{at and f' at{at}'}",
                     f"{said}{at and f' at{at}'} differs", [f[i][0] for f in fb],
                     [f[i][1:] for f in fb], [f[i][0] for f in fh], [f[i][1:] for f in fh])


def part_problems(bs: List[dict], hs: List[dict], extra: Optional[dict] = None
                  ) -> Tuple[List[str], List[Tuple[str, str]], bool]:
    """One part (a test, a fixture) in each BASE run (three) against each
    HEAD run (two): (the differences; the parts dropped as unstable at
    BASE, each (its key, in words, the BASE runs' values shown); whether
    the differences are only a loop's count differing by a few). Compared
    where the BASE runs agree, each by itself: each test-tree line's count;
    the assertions (method, site, then each argument); the children (argv
    and returncode, then the folder, each variable, each output line); each
    value read at the start (value_read); each function's return value and
    each local, a multiset over its calls. Where they disagree, finer
    (_compare): only what is unstable is dropped. The product lines every
    BASE run reached, each HEAD run must reach. A cut digest (DEPTH levels,
    LIMIT elements) is a part dropped. A difference is one any HEAD run
    shows. EXTRA, if given, gets `bounds`, `waived` and `unloaded` (_Cmp)."""
    c = _Cmp()
    loops: Set[str] = set()
    _lines_part(c, bs, hs, loops)
    _sequence(c, "assertion", "assertions", [r["asserts"] for r in bs],
              [r["asserts"] for r in hs], lambda x: f"{x[0]} at {_where(x[1])}", _assert_fine,
              _assert_finer, lambda x: x[1], loops)
    _sequence(c, "child process", "child processes", [r["children"] for r in bs],
              [r["children"] for r in hs], lambda x: f"`{' '.join(x[0])[:60]}`, exit {x[1]}",
              _child_fine, _child_finer, lambda x: " ".join(x[0])[:80], set())
    _values_part(c, bs, hs)
    _returns_part(c, bs, hs)
    need = set.intersection(*[{(f, n) for f, ns in r["product"].items() for n in ns}
                              for r in bs])
    miss = sorted(set().union(*[need - {(f, n) for f, ns in r["product"].items() for n in ns}
                                for r in hs]))
    if miss:
        c.found.append((f"{len(miss)} product line(s) BASE reached are not reached after: "
                        + ", ".join(f"{f}:{n}" for f, n in miss), "other"))
    cuts: Dict[str, str] = {}
    for r in bs:
        cuts.update(r.get("cuts", {}))
    for where, kind in sorted(cuts.items()):
        fam, _, rest = where.partition(" ")
        said = _return_name(rest) if fam == "local" else \
            f"the value of {_vname(rest)}" if fam == "value" else where
        c.drop(f"cut {where}", f"{said} cut at "
               + " and ".join(f"{DEPTH} levels" if k == "depth" else f"{LIMIT:,} elements"
                              for k in kind.split("/")))
    if extra is not None:
        extra.update(bounds=c.bounds, waived=c.waived, unloaded=c.unloaded)
    found = c.found
    loopish = bool(found) and all(k == "loop" for _t, k in found)
    return [t for t, _k in found], c.dropped, loopish


def _part_places(old: str, plan: "MT.SplitPlan", parts: dict, mods: Dict[str, str],
                 classes: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """Each part of BASE's run (PARTS) with where it runs at HEAD: (its key
    at BASE, its key at HEAD, the new file). A module fixture's is each
    file's copy; a class fixture's and a test's, its class's file."""
    out = []
    for key in parts:
        if key in ("setUpModule", "tearDownModule"):
            out += [(key, key, new) for new in plan.news]
            continue
        cls = key.split(" ", 1)[1] if key.startswith(("setUpClass ", "tearDownClass ")) else \
            key[len(old) + 1:].rpartition(".")[0] if key.startswith(f"{old}.") else None
        if cls in plan.placed:
            out.append((key, key if " " in key else rename_id(key, mods, classes),
                        plan.placed[cls]))
    return out


def base_problems(old: str, b1: dict) -> List[str]:
    """What BASE's run must be for a comparison to mean anything: every test
    passes, fails as expected, or is skipped by an unconditional
    `@unittest.skip` with a constant reason (the recorder's by_hand,
    resolved by identity); no fixture fails or skips. Anything else (a
    failure, an error, a test not run, a skip elsewhere, which where it
    runs may change what other classes see) refuses."""
    by_hand = set(b1.get("by_hand", []))
    bad = []
    for k, v in sorted(b1["tests"].items()):
        name = k[len(old) + 1:]
        if v == "pass" or v.startswith("expected failure"):
            continue
        if v.startswith("skip:") and (name in by_hand or name.rpartition(".")[0] in by_hand):
            continue
        bad.append(f"{name} ({v})")
    out = []
    if bad:
        out.append(f"outcomes: tests/{old}.py: {len(bad)} test(s) do not pass at BASE, so "
                   f"nothing shows what they do or check: {', '.join(bad)} (compared: a pass, an "
                   f"expected failure, an unconditional `@unittest.skip` with a constant "
                   f"reason): fix or prep the module so they pass here, or move it whole")
    if b1["held"]:
        out.append(f"outcomes: tests/{old}.py: a fixture fails or skips at BASE: "
                   f"{'; '.join(b1['held'])}: fix or prep the module so it runs here, or "
                   f"move it whole")
    return out


# The parts a batch accepts as unstable at BASE: a row each, read from
# HEAD's tree; the verifier lists each acceptance, for the batch's review.
UNSTABLE = "scripts/step9-unstable.tsv"


def unstable_text_problems(st: str, nm: str, text: Callable[[], str],
                           listed: List[str]) -> List[str]:
    """The text check of HEAD's UNSTABLE (status ST, mode NM, TEXT its
    blob's): the batch's acceptances of parts unstable at BASE, added,
    changed or deleted, put on LISTED for the batch's review (the outcome
    check names each one it uses); a row that is not one, or a mode but a
    plain file's, is a problem."""
    listed.append(UNSTABLE)
    if st == "D":
        return []
    try:
        rows = text()
    except UnicodeDecodeError:
        return [f"text: {UNSTABLE}: not text"]
    out = [f"text: {b}" for b in read_unstable(rows)[1]]
    return out + ([f"text: {UNSTABLE}: its mode is {nm}, not 100644"] if nm != "100644" else [])


# What a row's reason names: the unstable source (one of these words) and
# the line that makes it (`:NNN`); see the table's rules (the docstring).
UNSTABLE_SOURCES = ("time", "clock", "mtime", "stat", "pid", "thread", "poll", "random", "temp",
                    "port", "hash", "order", "environment")


def read_unstable(text: str) -> Tuple[Dict[Tuple[str, str], str], List[str]]:
    """UNSTABLE's rows (`test id<TAB>part key<TAB>why`; `#` a comment, a
    header row `test id ...`): ((the test id, the part key) -> why, the
    rows that are not one). The test id is the one after the split, or a
    fixture's `setUpClass Class (module)`, as the verifier names it. A
    reason names the unstable source (UNSTABLE_SOURCES) and its line
    (`:NNN`); a row that accepts a site's assertions or child processes
    whole is refused (prep the module instead)."""
    rows: Dict[Tuple[str, str], str] = {}
    bad: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if f[0] == "test id" and not rows:
            continue
        if len(f) != 3 or not all(x.strip() for x in f):
            bad.append(f"{UNSTABLE}:{n}: not `test id<TAB>part key<TAB>why`")
        elif (f[0], f[1]) in rows:
            bad.append(f"{UNSTABLE}:{n}: {f[0]} {f[1]} accepted twice")
        elif f[1].startswith(("assertions", "child processes")):
            what = "assertions" if f[1].startswith("assertions") else "child processes"
            bad.append(f"{UNSTABLE}:{n}: accepts a site's {what} whole: prep the module instead")
        elif not any(w in f[2].lower() for w in UNSTABLE_SOURCES) or \
                not re.search(r":\d+\b", f[2]):
            bad.append(f"{UNSTABLE}:{n}: the reason names no unstable source "
                       f"({', '.join(UNSTABLE_SOURCES)}) and its line (`:NNN`)")
        else:
            rows[(f[0], f[1])] = f[2]
    return rows, bad


def _compare_parts(old: str, plan: "MT.SplitPlan", bruns: List[dict],
                   hruns: Dict[str, List[dict]], mods: Dict[str, str], classes: Dict[str, str],
                   labels: Set[str]) -> tuple:
    """Each part of OLD's (_part_places) compared, BASE's runs BRUNS against
    HEAD's (part_problems), each labelled (the HEAD test id; a fixture's
    with its module, put on LABELS): (the parts that differ, each (label,
    the differences, loop only); the drops, the bounds and the digit runs
    left out, by label; the values not compared, by module)."""
    found: List[Tuple[str, List[str], bool]] = []
    drops: Dict[str, List[Tuple[str, str]]] = {}
    bounds: Dict[str, Dict[str, str]] = {}
    waived: Dict[str, List[str]] = {}
    unloaded: Dict[str, int] = {}
    b1 = bruns[0]
    for bkey, hkey, new in _part_places(old, plan, b1["parts"], mods, classes):
        label = hkey if " " not in hkey else f"{hkey} ({MT.dotted(new)})"
        labels.add(label)
        for j in range(len(b1["parts"][bkey])):
            bs = [[{**x, "imported": r.get("imported", {})} for x in r["parts"].get(bkey, [])[j:j + 1]]
                  for r in bruns]
            hs = [r["parts"].get(hkey, [])[j:j + 1] for r in hruns[new]]
            if not all(bs) or not all(hs):
                continue          # not run in one: the outcomes say
            ex: dict = {}
            d, dr, loopish = part_problems([x[0] for x in bs], [x[0] for x in hs], ex)
            if d:
                found.append((label, d, loopish))
            drops.setdefault(label, []).extend(x for x in dr if x not in drops.get(label, []))
            bounds.setdefault(label, {}).update(ex["bounds"])
            waived.setdefault(label, []).extend(ex["waived"])
            for m, k in ex["unloaded"].items():
                unloaded[m] = unloaded.get(m, 0) + k
    return found, drops, bounds, waived, unloaded


def outcome_problems(base: Path, head: Path, plans: Dict[str, "MT.SplitPlan"],
                     mods: Dict[str, str], classes: Dict[str, str], td: Path
                     ) -> Tuple[List[str], int, List[str]]:
    """Each split module run whole at BASE three times and each of its new
    files at HEAD twice (run_outcomes, recording), compared part by part
    (part_problems) under the renamed ids: a part all three BASE runs
    agree on must be the same in each HEAD run. Also: every test passing
    at BASE (base_problems), its outcome the same in every run; the
    fixtures' errors; the ids one to one, a duplicate each time
    (ids_problem); each test of a class a line places (one made by a call
    has another name); none that traces itself. A part the BASE runs
    disagree on (or a cut digest) refuses, unless HEAD's UNSTABLE accepts
    it. (The problems, the tests compared, the notes: each acceptance.)"""
    def rec(n: str, old: str) -> Dict[str, str]:
        # OLD's name is the module's too, as `<MOD>` (a path, a message naming it)
        return {"own": MT.dotted(n), "file": n, "alias": old}
    jobs: List[Tuple[tuple, Path, List[str], Dict[str, Union[str, bool]]]] = []
    for old, plan in plans.items():
        jobs += [(("base", old, k), base, [old], {"own": old, "file": f"{old}.py", "by_hand": True})
                 for k in (1, 2, 3)]
        jobs += [(("head", old, n, k), head, [MT.dotted(n)], rec(n, old))
                 for n in plan.news for k in (1, 2)]
    # In time, BASE's first two runs end before HEAD's start and its third
    # starts after they end: a value from the clock (a timestamp, a date)
    # that the three agree on is HEAD's too; one the clock moves between
    # them they disagree on, and it is dropped.
    runs: Dict[tuple, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        for phase in ([j for j in jobs if j[0][0] == "base" and j[0][2] < 3],
                      [j for j in jobs if j[0][0] == "head"],
                      [j for j in jobs if j[0][0] == "base" and j[0][2] == 3]):
            runs.update(zip([j[0] for j in phase],
                            ex.map(lambda j: run_outcomes(j[1], j[2], td, j[3]), phase)))
    out: List[str] = []
    notes: List[str] = []
    table = head / UNSTABLE
    accepted, bad_rows = read_unstable(table.read_text(encoding="utf-8")) if table.is_file() \
        else ({}, [])
    out += [f"outcomes: {b}" for b in bad_rows]
    used: Set[Tuple[str, str]] = set()
    labels: Set[str] = set()
    n = 0
    for old, plan in plans.items():
        bruns = [runs[("base", old, k)] for k in (1, 2, 3)]
        b1 = bruns[0]
        hruns = {new: [runs[("head", old, new, k)] for k in (1, 2)] for new in plan.news}
        want = {rename_id(k, mods, classes): v for k, v in b1["tests"].items()}
        n += len(want)
        flaky = sorted(k for k in set().union(*(r["tests"] for r in bruns))
                       if not _same([r["tests"].get(k) for r in bruns]))
        if flaky:
            out.append(f"outcomes: tests/{old}.py: {len(flaky)} test(s) end otherwise in the "
                       f"BASE runs, so nothing shows what they check: "
                       + ", ".join(f"{k} " + " / ".join(r["tests"].get(k, "absent") for r in bruns)
                                   for k in flaky)
                       + ": settle it by a prep edit, or move the module whole")
        out += base_problems(old, b1)
        for j in (0, 1):
            got: Dict[str, str] = {}
            held: List[str] = []
            for new in plan.news:
                got.update(hruns[new][j]["tests"])
                held += hruns[new][j]["held"]
            diff = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
            if diff:
                out.append(f"outcomes: tests/{old}.py: {len(diff)} test(s) differ after the split: "
                           + ", ".join(f"{k} {want.get(k, 'absent')} -> {got.get(k, 'absent')}"
                                       for k in diff))
            if _held(b1["held"]) != _held(held):
                out.append(f"outcomes: tests/{old}.py: the fixtures' errors differ: "
                           f"{sorted(_held(b1['held']) ^ _held(held))}")
            if diff or _held(b1["held"]) != _held(held):
                break             # the first HEAD run that differs says it
        ids = [i for new in plan.news for i in hruns[new][0].get("ids", [])]
        bad = ids_problem([rename_id(k, mods, classes) for k in b1.get("ids", [])], ids)
        if bad:
            out.append(f"outcomes: tests/{old}.py: the {bad}")
        unplaced = sorted({k[len(old) + 1:].rpartition(".")[0] for k in b1["tests"]
                           if k.startswith(f"{old}.")} - set(plan.placed))
        if unplaced:
            out.append(f"outcomes: tests/{old}.py: tests of {', '.join(unplaced)}, a class no "
                       f"line places (made by a call, or bound so): write it as a class of its "
                       f"own by hand first")
        tracers = sorted({k for r in bruns for k, recs in r["parts"].items()
                          if any(x.get("tracer") for x in recs)})
        if tracers:
            out.append(f"outcomes: tests/{old}.py: {', '.join(tracers)} trace(s) itself "
                       f"(sys.settrace, sys.setprofile or sys.monitoring), which the proof's "
                       f"recording uses: move the module whole")
        found, drops, bounds, waived, unloaded = _compare_parts(
            old, plan, bruns, hruns, mods, classes, labels)
        for label, d, loopish in found:
            out.append(f"outcomes: tests/{old}.py: {label} does or checks otherwise after the "
                       f"split: {'; '.join(d)}"
                       + (" (only a loop's count differs, by a few: rerun it; the same counts "
                          "again are real)" if loopish else ""))
        for label, w in waived.items():
            if w:
                notes.append(f"digit runs left out, the BASE runs disagreeing on them (BASE's "
                             f"-> HEAD's, each within BASE's range widened by its spread): "
                             f"{label}: {'; '.join(w)}")
        if unloaded:
            notes.append(f"tests/{old}.py: {sum(unloaded.values())} value(s) BASE read not "
                         f"compared, their module not loaded at the part's start after the split: "
                         + ", ".join(f"{m} {k}" for m, k in sorted(unloaded.items(),
                                                                   key=lambda x: (-x[1], x[0]))))
        for label, dr in drops.items():
            left = []
            for key, text in dr:
                why = accepted.get((label, key))
                if why is None:
                    left.append(f"{text} (`{key}`)")
                    continue
                used.add((label, key))
                notes.append(f"accepted as unstable ({UNSTABLE}): {label}: `{key}`: {why}")
                if key in bounds.get(label, {}):
                    out.append(f"outcomes: tests/{old}.py: {label}: `{key}`, accepted as unstable, "
                               f"breaks its bounds: {bounds[label][key]}")
            if left:
                out.append(f"outcomes: tests/{old}.py: {label}: {len(left)} part(s) differ between "
                           f"the BASE runs, so nothing shows them the same after the split: "
                           f"{'; '.join(left)}: settle them by a prep edit, or accept each in "
                           f"{UNSTABLE} (the test id, the part key, why)")
    for label, key in sorted(set(accepted) - used):
        if label in labels:
            notes.append(f"{UNSTABLE} accepts {label} `{key}`, which no BASE run dropped this "
                         f"time: the row goes")
    return out, n, notes


def ids_problem(b: List[str], h: List[str]) -> Optional[str]:
    """BASE's ids (renamed) B against HEAD's H, one to one: what is missing
    or new, a lost or doubled duplicate named with its count."""
    cb, ch = collections.Counter(b), collections.Counter(h)
    if cb == ch:
        return None
    miss, extra = cb - ch, ch - cb

    def name(c: "collections.Counter[str]") -> List[str]:
        return [k if c[k] == 1 else f"{k} (x{c[k]})" for k in sorted(c)]
    return (f"ids: {sum(miss.values())} of BASE's (renamed) missing, {sum(extra.values())} new: "
            f"{name(miss) + name(extra)}")


def summary_problems(base_run, head_run) -> List[str]:
    """`check_tax_rules --summary` in BASE and HEAD (completed processes):
    the same output, and exit 0 in both."""
    out = []
    if base_run.stdout != head_run.stdout:
        out.append("pins: check_tax_rules --summary differs")
    for name, r in (("BASE", base_run), ("HEAD", head_run)):
        if r.returncode != 0:
            out.append(f"pins: check_tax_rules --summary fails in {name} (exit {r.returncode})")
    return out


def _test_cmds(tree: Path) -> List[str]:
    t = ast.parse((tree / "scripts" / "mutation_audit.py").read_text(encoding="utf-8"))
    for n in t.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TEST_CMDS":
            return [c.value for c in ast.walk(n.value) if isinstance(c, ast.Constant)
                    and isinstance(c.value, str) and re.fullmatch(r"[\w.]*test_\w+(\.\w+)*", c.value)]
    return []


def run_base_copy(repo: Path, base: str, head: str, flags: List[str]) -> int:
    """Run BASE's copy of this script (which loads BASE's mover and
    moved.py) on the same two trees: a batch cannot change its own proof."""
    with tempfile.TemporaryDirectory(prefix="verify-moves-tools-") as td:
        try:
            export(base, Path(td), repo, TOOLS)
        except subprocess.CalledProcessError:
            print(f"verify: BASE lacks one of {', '.join(TOOLS)}: the tools land in a commit "
                  f"of their own before a batch", file=sys.stderr)
            return 2
        me = Path(td) / "scripts" / "verify_test_moves.py"
        if "--base-copy" not in me.read_text(encoding="utf-8"):
            print("verify: BASE's verifier predates running as BASE's copy (TJ-3704): "
                  "verify from a later base", file=sys.stderr)
            return 2
        env = {k: v for k, v in _genv().items() if k != "PYTHONPATH"}
        return subprocess.run([sys.executable, "-I", "-S", str(me), base, head,
                               f"--repo={repo}", "--base-copy", *flags], env=env).returncode


def verify(repo: Path, base_rev: str, head_rev: str, no_ids: bool) -> int:
    problems: List[str] = []
    with tempfile.TemporaryDirectory(prefix="verify-moves-") as td:
        base, head, home = (Path(td) / n for n in ("base", "head", "home"))
        for d in (base, head, home):
            d.mkdir()
        export(base_rev, base, repo)
        export(head_rev, head, repo)
        mods, classes = batch_moves(base, head)
        splits = split_moves(base, head)
        paths = {f"tests/{o}.py": f"tests/{d.replace('.', '/')}.py" for o, d in mods.items()}
        print(f"verify: {len(mods)} module(s) moved whole, {len(classes)} class(es) split "
              f"({len(splits)} module(s) into {sum(len(r) for r in splits.values())} file(s)), "
              f"{base_rev[:12]} to {head_rev[:12]}")

        # 2. text
        bad, listed, n = text_problems(repo, base_rev, head_rev, base, head, mods)
        print(f"verify: text: {n} file(s) changed; beyond the renames, by hand "
              f"(docstrings, docs, settled names): {listed or 'none'}")
        if bad:
            # Nothing of HEAD's runs: a batch that fails here is not BASE's
            # code renamed (TJ-3744).
            for problem in bad:
                print(f"verify: {problem}")
            print("verify: FAILED (the text check; nothing of HEAD's was run)")
            return 1

        # 3. pins
        pins = {}
        for name, tree in (("base", base), ("head", head)):
            out = Path(td) / f"pins-{name}.json"
            subprocess.run([sys.executable, "-c", _PINS, str(out)], cwd=tree,
                           env=_env(tree, str(home)), check=True)
            pins[name] = json.loads(out.read_text())
        plans, _refused = split_plans(base, head, splits)     # the text check passed
        held = held_names(plans)
        bp = {k2: v for k, v in pins["base"].items() for k2 in pin_keys(k, mods, classes, held)}
        if bp != pins["head"]:
            ks = sorted(k for k in set(bp) | set(pins["head"]) if bp.get(k) != pins["head"].get(k))
            problems.append(f"pins: {len(ks)} test(s) pin differently: {ks}")
        problems += summary_problems(*[
            subprocess.run([sys.executable, "scripts/check_tax_rules.py", "--summary"],
                           cwd=t, env=_env(t, str(home)), capture_output=True, text=True)
            for t in (base, head)])
        print(f"verify: pins: {len(pins['head'])} marked tests")

        # 4. wiring
        hs, bs = _stage_sets(head), _stage_sets(base)
        problems += wiring_problems(bs, hs, mods, splits, classes, stage_picks(base, head, splits))
        bt = [rename_id(x, mods, classes) for x in _test_cmds(base)]
        if bt != _test_cmds(head):
            problems.append("wiring: mutation_audit's TEST_CMDS do not name BASE's modules, renamed")
        bare = sorted({x for x in _test_cmds(base) if x.split(".")[0] in splits
                       and ".".join(x.split(".")[:2]) not in classes})
        if bare:
            problems.append(f"wiring: mutation_audit's TEST_CMDS run {bare}, split, as a "
                            f"module: name its classes there before the split")
        gone = {f"tests/{o}.py" for o in splits}
        new = {f"tests/{r}" for rels in splits.values() for r in rels}
        bb = without_files(MT.rekey_baseline(json.loads((base / MT.BASELINE).read_text()), paths),
                           gone)
        hb = without_files(json.loads((head / MT.BASELINE).read_text()), new)
        for kind in ("ruff", "noqa"):
            if bb.get(kind) != hb.get(kind):
                problems.append(f"wiring: the architecture baseline's {kind} counts are not BASE's re-keyed")
        problems += split_baseline_problems(base, head, splits, plans)
        print(f"verify: wiring: no-extras {len(hs[0])}, release-edits {len(hs[1])} modules; "
              f"leaving a stage (their tests run in the suite): "
              f"{stage_leavers(bs, hs, splits) or 'none'}")

        # 1. ids
        if not no_ids:
            got = {}
            for name, tree in (("base", base), ("head", head)):
                out = Path(td) / f"ids-{name}.json"
                subprocess.run([sys.executable, "-c", _COLLECT, str(out)], cwd=tree / "tests",
                               env=_env(tree, str(home)), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                got[name] = json.loads(out.read_text())
                for e in got[name]["errors"]:
                    problems.append(f"ids: {name} fails to load a module: {e}")
            b = [rename_id(t, mods, classes) for t in got["base"]["ids"]]
            h = got["head"]["ids"]
            bad_ids = ids_problem(b, h)
            problems += [bad_ids] if bad_ids else []
            print(f"verify: ids: {len(h)} in HEAD, {len(b)} in BASE renamed")

        # 5. outcomes (a split's)
        if plans:
            t0 = time.monotonic()
            bad, n, notes = outcome_problems(base, head, plans, mods, classes, Path(td))
            problems += bad
            print(f"verify: outcomes: {n} test(s) of {len(plans)} split module(s), what each "
                  f"does and checks recorded (BASE three times, HEAD twice), in "
                  f"{time.monotonic() - t0:.0f}s")
            for note in notes:
                print(f"verify: outcomes: {note}")
    for p in problems:
        print(f"verify: {p}")
    print("verify: " + ("FAILED" if problems else "the moves changed nothing but names"))
    return 1 if problems else 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a in ("--no-ids", "--trees")]
    repo = REPO
    for a in argv:
        if a.startswith("--repo="):
            repo = Path(a[len("--repo="):]).resolve()
        elif a.startswith("--") and a not in ("--no-ids", "--trees", "--base-copy"):
            args.append(a)        # unknown: the usage
    if len(args) > 2 or any(a.startswith("--") for a in args):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    bad = rewritten_history(repo)
    if bad:
        print(f"verify: {bad}: git would not show the objects themselves", file=sys.stderr)
        return 2
    head_rev = args[1] if len(args) > 1 else "HEAD"
    head, hkind = resolve(repo, head_rev)
    base, bkind = resolve(repo, args[0] if args else default_base(repo, head))
    if "--trees" not in flags:
        for name, kind in (("BASE", bkind), ("HEAD", hkind)):
            if kind != "commit":
                print(f"verify: {name} is a tree, not a commit (--trees: a synthetic "
                      f"repository's)", file=sys.stderr)
                return 2
        bad = ancestry_problem(repo, base, head)
        if bad:
            print(f"verify: {bad}", file=sys.stderr)
            return 2
    if "--base-copy" not in argv:
        return run_base_copy(repo, base, head, flags)
    if sys.version_info < (3, 12):
        # The outcome check records each test with sys.monitoring (3.12):
        # BASE's copy, the one that checks, refuses to run without it.
        print(f"verify: needs Python 3.12 or later (sys.monitoring records what each test does), "
              f"not {sys.version.split()[0]}: run it with the dev venv's python3", file=sys.stderr)
        return 2
    return verify(repo, base, head, "--no-ids" in flags)


if __name__ == "__main__":
    sys.exit(main())

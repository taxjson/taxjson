#!/usr/bin/env python3
"""extract_support: move what test modules share into tests/_support/
(PLAN.md step 9 B1).

Usage: scripts/extract_support.py TABLE [--dry-run] [--repo=PATH]
       scripts/extract_support.py --check [BASE [HEAD]] [--allow=PATH,...]
                                  [--no-ids] [--trees] [--repo=PATH]

TABLE has a line per source module, `SOURCE SUPPORT DESCRIPTION`
(`test_fix_ibparse ib IB statement rows and the parse helpers`). Every
name another test module imports from tests/SOURCE.py (any test module
below tests/ or packages/*/tests, at any depth), and the module-level
names those reach, move to a new tests/_support/SUPPORT.py: their text as
it was, the comment lines directly above them with it, and the imports
they use. SOURCE then imports back the moved names it still reads and
drops the imports only they read; the import from the support module
goes where the first dropped import of a test-tree helper was (what that
ran, the hermetic setup, runs at the same point), or after the imports;
with nothing to import back, `import _support` goes there (TJ-3845).
Each `from SOURCE import a, b` becomes
`from _support.SUPPORT import a, b`; CROSS_IMPORTS_MAX
(tests/test_test_layout.py) becomes the new count. The sources go in
name order, one deferred until the source whose names it needs is done.
A dry run plans each source on the tree as it is and writes nothing.

Refused, before anything is written (each by hand, in a commit of its
own, first):
  - no tests/SOURCE.py, or a SUPPORT that exists;
  - a name imported from SOURCE that is no module-level def, class or
    assignment there (a re-export), or one it binds more than once
    (twice, also by an import, in an `if` or `try`, `del`, `global`);
  - a class among the names that runs tests where it is imported (its
    tests to a mixin, by hand): a TestCase with tests of its own or of a
    base of SOURCE's, or with a base from elsewhere, or a class with tests
    on a base from elsewhere; a fixture base (a TestCase with setUp,
    setUpClass or helpers, no tests) or a mixin moves as it is (TJ-3946);
    or unittest's module hooks (`setUpModule`, `tearDownModule`,
    `load_tests`: SOURCE keeps them);
  - a statement that binds a name that moves and one that stays, or
    shares its line with another;
  - moved code that reads where it runs (`__file__`, `__name__`,
    `__spec__`, `__doc__`, `globals`, `vars()`, `locals()`, `eval`,
    `exec`, inspect's and traceback's readers of a file, a source or the
    stack, a frame, a function's code or globals, an object's
    `__module__`, its own `sys.modules` entry; the attributes above, also
    by a string equal to one: TJ-4006, TJ-4008), or a name SOURCE binds
    in an `if` or `try`; code that stays and reads SOURCE's own names
    by name (`globals`, `vars()`, `sys.modules[__name__]`); a star import
    in SOURCE (what it binds is not known);
  - code that stays in SOURCE and, at import, changes what a moved name
    or an import holds (a store into it, a method that changes it,
    `global`), calls a def of SOURCE that changes module-level state
    (through the defs it calls), or passes a moved name (but a constant)
    to an imported function or to a def or class of SOURCE that can
    change its argument; one that reads a moved name above its definition; any
    other code SOURCE runs at import but its imports, definitions and
    assignments (an importer of the moved names would no longer run
    it). Reading a moved name at import is the same in either place;
  - an import the names need from a test module (not in TABLE), or a
    relative one;
  - another module's `import SOURCE` (attribute reads), star import or
    `from pkg.SOURCE import`, or SOURCE's of itself; a string naming a
    moved name as `SOURCE.NAME` (a patch target), a call given SOURCE's
    name and a moved one (`patch.multiple`), in a SOURCE that reads
    `__name__` a string ending `.NAME`; SOURCE's path (`.../SOURCE.py`,
    or `SOURCE.py` joined to a path or passed to a call). A string naming
    SOURCE or a name that stays, or `SOURCE.py` in a list of names, is
    left as it is.

Left to --check's ids stage (3), not refused here: tests the class
rule does not read (added by a class decorator, a `def test_*` under an
`if`, a nested TestCase, a TestCase `type(...)` builds, names bound by a
tuple assignment); a batch that moves such a class where its tests then
run fails there (TJ-4004). What no reading of the code can promise,
--check observes (2).

--check: a batch's proof, run as BASE's copy (from git, isolated, never
the checkout's, as scripts/verify_test_moves.py runs its own):

    T=$(mktemp -d) && git --no-replace-objects archive BASE \\
      scripts/extract_support.py scripts/move_tests.py \\
      scripts/verify_test_moves.py scripts/moved.py |
      tar -x -C "$T" && python3 -I -S "$T/scripts/extract_support.py" \\
      --check BASE HEAD --repo=.

  1. text: the table is HEAD's new tests/_support/ modules (each one's
     `Shared from:` line and description); BASE's tool, run on BASE's
     files with it, then BASE's check_architecture
     --accept-growth=ruff,noqa (the hits that moved with their code),
     write HEAD's: every path, mode and blob of HEAD's tree (git's
     objects, as git holds them: no `.gitattributes` hides or converts
     one; an entry twice, a symlink or a submodule refused). Beyond that
     only the .md docs --allow names, outside tests/, may differ: listed,
     for review. A batch that fails it has none of its code run.
  2. state. What it proves, for each source, in fresh interpreters of
     both trees that import as the runner and discovery do (from tests/,
     after the hermetic setup, offline, each with a HOME of its own):
     (a) BASE's `import SOURCE` and HEAD's `import _support.SUPPORT`
     give the moved names the same values, and both trees' `import
     SOURCE` the names HEAD's keeps (HEAD's lacks only the moved names
     and the imports only they read), also run alone without the setup;
     (b) the rest of the process is the same: the environment, sys.path,
     the decimal context, the random state, the warnings filters (in
     order) and the function that shows one, the import system's finders
     and path hooks (by module and name), logging's disable level and the
     root logger's level and handlers, the umask, what lies below each
     HOME the run knows (each path by its name there, a file with its
     size), the fake modules and the names of each module of the
     repository both imports loaded; (c) each module of the repository
     BASE's import loaded and HEAD's did not (a lost module) changes none
     of (b) when HEAD imports it (what a source's or the setup's own
     import does is (b)'s); (d) a lost module's import-time state (a
     source's and the setup's too, but not the support package's or this
     batch's support modules'), its own names and those of every module
     of the repository its import loads, and what it changes of the
     environment, sys.path, the decimal context, (b)'s process views and
     the fake modules (TJ-3976, TJ-3991), is the same when a test changes
     the environment before it imports it first: HOME moved, the variables
     below it with it and the variables the tree's tests set or remove
     (`test_env`) each given a value of its own (TAXJSON_OFFLINE another
     spelling of on: it keeps its truthiness); when that import reads a
     variable the tests remove (or walks the environment), the same with
     those unset too; when it reads one the tests give a constant, the
     same with each at each of its constants (CONSTANT_FORKS at most, a
     note naming any left out); and no such import touches a path below
     its moved HOME. A lost module that fails (d) fails the check when a
     real importer of the moved names no longer loads it and may import it
     in a test, and always in the source's own views.
     Left to the suite: the clock, the working folder, the network,
     randomness and every other input that is not the environment or a
     path below HOME; a variable read through C (time.tzset's TZ);
     TAXJSON_OFFLINE off or unset, and HOME or the setup's record of it
     unset (the check runs offline, under a HOME); a value a test
     computes, past its constants, and a variable it names only by a
     computed string (TJ-4001); a file's contents below HOME but its
     size, and what lies there past HOME_MAX entries (a note says so); a
     cache's entries, but their number and, for a function without
     parameters, the value of its one entry (TJ-3977); the process's
     other state (loggers but the root, signal handlers, threads,
     atexit, sys's hooks and limits, the locale, the codec and mimetypes
     registries, a package's own state); state reachable only through a
     call (TJ-3846); what a module of the standard library or a package
     does at its import; a module a test imports by a name it computes.

     How. Values are walked deep (containers, functions' defaults and
     closures, a generator's frame locals, place and delegate, classes'
     attributes and dunders, nested classes, objects' attributes and
     slots and, with an outside base, their reduce, a decimal context's
     settings, a cache wrapper's function and number of entries
     (`functools.lru_cache`, `cache`, on a method too, less the place
     `update_wrapper` copies into it, TJ-4013; for a function
     without parameters, the value of its one entry, read by a call the
     cache answers: the function the wrapper holds, not a `__wrapped__`
     put in its place, TJ-3992; one whose function it cannot find is cut
     short, TJ-4005), a fake module's names, the live
     environment by name, others by repr or pickle; a
     walk cut short fails, and so does a value it sees by its type alone:
     no attributes of its own, or a repository object whose outside base
     neither its reduce nor its pickle reads). (b) leaves out the
     product's named memos (MEMOS: a module's name; for a cache wrapper,
     CACHED, its entries wherever it is held, the wrapper itself walked,
     so a store that replaces it is seen: TJ-3975); a fake module is one
     without a spec or with a bare one, or planted under another's name;
     the random state is seeded first. (c): the world is taken, the
     modules outside the repository a lost module imports are imported
     (what they do is theirs), then it, its parent packages and its
     imports of the repository's; then each module BASE loaded is loaded
     on HEAD and (b) compared over all the modules both loaded. (d) is a
     replay of each lost module (TJ-3933, TJ-3967): forks of the process
     as it stood
     after HEAD's import (the setup and that module, nothing after) import
     it, one as it is and the others with the environment changed just
     before, each moved one with a HOME of its own (the setup's record of
     its HOME, SETUP_HOME, kept); each digests every module of the
     repository its import loaded and records the variables it read
     (os.environ's and environb's by name, all of them when walked or
     copied) and, after it, the process's views (`process_view`,
     sys.path, the decimal context, what it changed of the environment:
     TJ-3976; the fake modules: TJ-3991); a moved one records each path
     below its HOME its import
     gives an audited call (TJ-3964: open; os's listdir, scandir, walk,
     fwalk, mkdir, rmdir, remove, rename, link, symlink, chmod, chown,
     chflags, utime, truncate, chdir and xattr calls; shutil's copies,
     moves, removals and archives; tempfile's mkdtemp and mkstemp; glob's
     and pathlib's globs; sqlite3.connect; ctypes.dlopen) or os.stat,
     lstat, access, readlink, statvfs, mkfifo or mknod (wrapped while it
     imports), a path that is not there too. Each fork leads a session of
     its own, killed with what it started at FORK_TIMEOUT or its end
     (TJ-3970); the HOMEs the run knows are copied first and put back
     after a fork that changed one. A name that differs from the unmoved
     fork's (but one that differs as well between two forks that move
     nothing: the clock, randomness) or is cut short moves the module
     that holds it (a lost module too), a module only one fork loaded, a
     view of the process that differs so or a path touched moves the one
     imported, and a fork that fails (that one too) fails it. It needs
     os.fork: without it each lost module fails as one whose replay
     fails. A real importer: a
     test module of tests/ or of a package's tests whose imports on
     BASE reach the source through test modules, at module level, in a
     function, by `import_module` or `__import__` of a constant, or by a
     string naming one (over-counted rather than missed: TJ-3935),
     imported alone in a child of its own on each tree as its runner
     imports it; it no longer loads a module when its BASE import loads
     it and its HEAD import does not (a child that fails counts); it may
     import it in a test when that is a module of the product, or a test
     helper a file its HEAD import loaded imports in a function or names
     in a string, or one those reach, in a function too (or, when its
     HEAD import fails, any test helper). Run alone (no preload, as
     `python -m unittest SOURCE` runs it): HEAD's import running the
     hermetic setup BASE's did not is a note, and that view is then
     compared with BASE's import followed by the setup, the setup's HOME
     and the one given as one (TJ-3969); the reverse is a failure. Two
     runs of BASE's own import that differ fail the check, each
     difference named.
  3. ids: unittest's discovery in both trees, tests/ and each
     packages/*/tests, gives the same test ids, each with the same
     markers and skip on its function and its class (tests/tax_rules'
     `__tax_rules__`, unittest's `__unittest_skip__` and its reason:
     TJ-3946), bytecode off (--no-ids skips it: it imports every test
     module twice).
--trees lets BASE and HEAD be trees (the tests' synthetic repositories).
Exit 0 with a line per check; 1 with each difference; 2 when the
arguments name no batch to check.
"""
from __future__ import annotations

import ast
import concurrent.futures
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SUPPORT = Path("tests") / "_support"
MARK = "Shared from: "
HEADLINE = "Shared test helpers: "
LAYOUT = Path("tests") / "test_test_layout.py"
WIDTH = 99
TOOLS = ("scripts/extract_support.py", "scripts/move_tests.py",
         "scripts/verify_test_moves.py", "scripts/moved.py")
# Names that read where the code runs: moved, they would read the support
# module's instead.
PLACE = frozenset({"__file__", "__name__", "__spec__", "__package__", "__loader__",
                   "__path__", "__builtins__", "__cached__", "__doc__", "eval", "exec"})
# Attributes that read a frame's, a function's or an object's own module or file.
PLACE_ATTRS = frozenset({"__code__", "co_filename", "__globals__", "f_globals", "f_locals",
                         "f_code", "f_back", "_getframe", "__module__"})
# The functions of inspect and traceback that read where code lives or runs.
LOCATION = {"inspect": frozenset({"getsourcefile", "getfile", "getabsfile", "getsource",
                                  "getsourcelines", "findsource", "getcomments", "getmodule",
                                  "getmodulename", "currentframe", "stack", "trace",
                                  "getframeinfo", "getouterframes", "getinnerframes"}),
            "traceback": frozenset({"extract_stack", "format_stack", "print_stack",
                                    "walk_stack"})}
# unittest's module hooks: they stay in the test module that runs them.
HOOKS = frozenset({"setUpModule", "tearDownModule", "load_tests"})
# Methods that change the object they are called on.
MUTATORS = frozenset({"append", "extend", "insert", "add", "update", "setdefault", "pop",
                      "popitem", "remove", "discard", "clear", "sort", "reverse", "register",
                      "__setitem__", "__delitem__"})
# A section's banner (`# ----- IB corporate actions`): it stays, with its section.
BANNER = re.compile(r"#\s*([-=#*~_])\1{2,}")
INIT_DOC = ('"""What test modules share: helpers, fixture text and rows (PLAN.md\n'
            'step 9 B1). A test module imports from here, never from another test\n'
            'module (tests/test_test_layout.py).\n"""\n')
# A module run alone that imports a support module gets the synthetic HOME
# (tests/_hermetic), as it did through the source's helpers (TJ-3829).
INIT_HERMETIC = "\nimport _hermetic  # noqa: F401  (a synthetic HOME, offline: tests/_hermetic)\n"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_extract_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MT = _load("move_tests")


class Refused(Exception):
    def __init__(self, why: str, needs: Optional[str] = None):
        super().__init__(why)
        self.needs = needs        # the test module whose names this source needs first


# Scopes: which module-level names a statement reads.

class _Scope:
    def __init__(self, kind: str, parent: Optional["_Scope"] = None):
        self.kind, self.parent = kind, parent
        self.binds: Set[str] = set()
        self.reads: Set[str] = set()
        self.declared: Set[str] = set()     # global and nonlocal
        self.children: List["_Scope"] = []

    def child(self, kind: str) -> "_Scope":
        c = _Scope(kind, self)
        self.children.append(c)
        return c

    def free(self) -> Set[str]:
        """The names this scope and those in it read from outside it."""
        if self.kind == "class":          # a class body's names are not its methods'
            return set(self.reads) | {n for c in self.children for n in c.free()}
        local = self.binds - self.declared
        inner = {n for c in self.children for n in c.free()}
        return {n for n in self.reads | inner if n not in local}


def _bind_args(args: ast.arguments, sc: _Scope) -> None:
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if a is not None:
            sc.binds.add(a.arg)


def _outer_args(args: ast.arguments, sc: _Scope) -> None:
    for d in [*args.defaults, *[d for d in args.kw_defaults if d is not None]]:
        _walk(d, sc)
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if a is not None and a.annotation is not None:
            _walk(a.annotation, sc)


def _walk(node: ast.AST, sc: _Scope) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        sc.binds.add(node.name)
        for d in node.decorator_list:
            _walk(d, sc)
        _outer_args(node.args, sc)
        if node.returns is not None:
            _walk(node.returns, sc)
        inner = sc.child("function")
        _bind_args(node.args, inner)
        for s in node.body:
            _walk(s, inner)
    elif isinstance(node, ast.Lambda):
        _outer_args(node.args, sc)
        inner = sc.child("function")
        _bind_args(node.args, inner)
        _walk(node.body, inner)
    elif isinstance(node, ast.ClassDef):
        sc.binds.add(node.name)
        for x in [*node.decorator_list, *node.bases, *[k.value for k in node.keywords]]:
            _walk(x, sc)
        inner = sc.child("class")
        for s in node.body:
            _walk(s, inner)
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        gens = node.generators
        _walk(gens[0].iter, sc)
        inner = sc.child("comprehension")
        for i, g in enumerate(gens):
            _walk(g.target, inner)
            if i:
                _walk(g.iter, inner)
            for c in g.ifs:
                _walk(c, inner)
        for part in ((node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)):
            _walk(part, inner)
    elif isinstance(node, ast.NamedExpr):
        target = sc
        while target.kind == "comprehension" and target.parent is not None:
            target = target.parent
        target.binds.add(node.target.id)
        _walk(node.value, sc)
    elif isinstance(node, ast.Name):
        (sc.reads if isinstance(node.ctx, ast.Load) else sc.binds).add(node.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        sc.binds.update((a.asname or a.name).split(".")[0] for a in node.names)
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        sc.declared.update(node.names)
    else:
        name = getattr(node, "name", None)        # except ... as name, match captures
        if isinstance(name, str) and isinstance(node, (ast.ExceptHandler, ast.MatchAs,
                                                       ast.MatchStar)):
            sc.binds.add(name)
        rest = getattr(node, "rest", None)
        if isinstance(rest, str) and isinstance(node, ast.MatchMapping):
            sc.binds.add(rest)
        for c in ast.iter_child_nodes(node):
            _walk(c, sc)


def module_reads(stmt: ast.stmt) -> Set[str]:
    """The module-level names STMT reads: at module level, and in its
    functions, classes and comprehensions each name none of them binds
    (a name a function assigns anywhere is its own: Python's rule)."""
    sc = _Scope("module")
    _walk(stmt, sc)
    return sc.reads | {n for c in sc.children for n in c.free()}


def module_binds(stmt: ast.stmt) -> Set[str]:
    sc = _Scope("module")
    _walk(stmt, sc)
    return sc.binds


SysNames = Tuple[Set[str], Set[str]]


def sys_names(imports: Dict[str, list], tree: Optional[ast.AST] = None) -> SysNames:
    """The names a module binds to sys (`import sys as s`) and to
    sys.modules (`from sys import modules`) (TJ-3834), at module level
    or, in TREE, in any function (TJ-3847)."""
    pairs = [(st, a) for sa in imports.values() for st, a in sa] + [
        (st, a) for st in (ast.walk(tree) if tree is not None else ())
        if isinstance(st, (ast.Import, ast.ImportFrom)) for a in st.names]
    return ({"sys"} | {a.asname or a.name for st, a in pairs
                       if isinstance(st, ast.Import) and a.name == "sys"},
            {a.asname or a.name for st, a in pairs
             if isinstance(st, ast.ImportFrom) and st.module == "sys" and a.name == "modules"})


def _is_sys_modules(x: ast.AST, names: SysNames = ({"sys"}, set())) -> bool:
    return (isinstance(x, ast.Attribute) and x.attr == "modules"
            and isinstance(x.value, ast.Name) and x.value.id in names[0]) \
        or (isinstance(x, ast.Name) and x.id in names[1])


def _names_self(x: ast.AST, source: str) -> bool:
    """X can name SOURCE's module: `__name__`, SOURCE, or any `.__module__`
    (`type(self).__module__`, TJ-3847)."""
    return any((isinstance(a, ast.Name) and a.id == "__name__")
               or (isinstance(a, ast.Attribute) and a.attr == "__module__")
               for a in ast.walk(x)) or (isinstance(x, ast.Constant) and x.value == source)


# The attributes that hand over a module's namespace (own_namespace): a
# function's globals are its module's, getmodule(obj) is obj's module.
GLOBALS_READS = {"f_globals": "a frame's f_globals", "__globals__": "a function's __globals__",
                 "getmodule": "inspect.getmodule"}


def own_namespace(node: ast.AST, source: str,
                  names: SysNames = ({"sys"}, set())) -> List[str]:
    """How NODE reads its own module's names by name (TJ-3820): `globals`,
    `vars()` or `locals()` with no argument, `sys.modules[__name__]` (or
    SOURCE's name, or a `.__module__`, by subscript or `.get`; sys by any
    of NAMES), `import_module(__name__)` or `__import__(__name__)`
    (TJ-3847), a frame's `f_globals` (TJ-3871), a function's `__globals__`
    and `inspect.getmodule` (TJ-3911), by attribute or by a string
    (`getattr(f, "__globals__")`); a read of `sys.modules` by anything but
    a constant (`sys.modules[name]`, `.get(name)`: the name may be its
    own). `vars(obj)` and another module's `sys.modules` entry by its
    constant name are no such read."""
    out = []
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id == "globals":
            out.append("globals")
        elif isinstance(x, ast.Call) and isinstance(x.func, ast.Name) \
                and x.func.id in ("vars", "locals") and not x.args and not x.keywords:
            out.append(f"{x.func.id}()")
        elif isinstance(x, ast.Subscript) and _is_sys_modules(x.value, names) \
                and _names_self(x.slice, source):
            out.append("sys.modules[__name__]")
        elif isinstance(x, ast.Subscript) and _is_sys_modules(x.value, names) \
                and isinstance(x.ctx, ast.Load) and not isinstance(x.slice, ast.Constant):
            out.append("sys.modules[a name]")
        elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) \
                and _is_sys_modules(x.func.value, names) and x.args \
                and _names_self(x.args[0], source):
            out.append("sys.modules[__name__]")
        elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) \
                and x.func.attr == "get" and _is_sys_modules(x.func.value, names) and x.args \
                and not isinstance(x.args[0], ast.Constant):
            out.append("sys.modules[a name]")
        elif isinstance(x, ast.Call) and x.args and _names_self(x.args[0], source) and (
                (isinstance(x.func, ast.Name) and x.func.id in ("import_module", "__import__"))
                or (isinstance(x.func, ast.Attribute) and x.func.attr == "import_module")):
            out.append("import_module(__name__)")
        elif isinstance(x, ast.Attribute) and x.attr in GLOBALS_READS:
            out.append(GLOBALS_READS[x.attr])         # (sys._getframe().f_globals: TJ-3871)
        elif isinstance(x, ast.Constant) and x.value in GLOBALS_READS:
            out.append(GLOBALS_READS[x.value])        # (getattr(f, "__globals__"): TJ-3911)
        elif isinstance(x, ast.Name) and x.id == "getmodule":
            out.append(GLOBALS_READS["getmodule"])    # (from inspect import getmodule)
    return out


def location_reads(node: ast.AST, imports: Dict[str, list]) -> List[str]:
    """NODE's calls of inspect's or traceback's location readers
    (`inspect.getsourcefile`, a name imported from either)."""
    mods = {n: stmt_a[1].name for n, sa in imports.items() for stmt_a in sa
            if isinstance(stmt_a[0], ast.Import) and stmt_a[1].name in LOCATION}
    froms = {n for n, sa in imports.items() for st, a in sa
             if isinstance(st, ast.ImportFrom) and st.module in LOCATION
             and a.name in LOCATION[st.module]}
    out = []
    for x in ast.walk(node):
        if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) \
                and x.value.id in mods and x.attr in LOCATION[mods[x.value.id]]:
            out.append(f"{mods[x.value.id]}.{x.attr}")
        elif isinstance(x, ast.Name) and x.id in froms:
            out.append(x.id)
    return out


def _place_attrs(node: ast.AST) -> List[str]:
    """The PLACE_ATTRS NODE reads: by attribute, or by a string
    (`getattr(o, "__module__")`, TJ-4006)."""
    return sorted({x.attr for x in ast.walk(node)
                   if isinstance(x, ast.Attribute) and x.attr in PLACE_ATTRS}
                  | {x.value for x in ast.walk(node) if isinstance(x, ast.Constant)
                     and isinstance(x.value, str) and x.value in PLACE_ATTRS})


def import_time_nodes(stmt: ast.stmt) -> List[ast.AST]:
    """The nodes of STMT that run when it runs at module level: all its
    code but the bodies of the functions and lambdas it defines (a class
    body runs; decorators, defaults and annotations are evaluated)."""
    out: List[ast.AST] = []
    todo: List[ast.AST] = [stmt]
    while todo:
        n = todo.pop()
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            todo += [*getattr(n, "decorator_list", ()), *a.defaults,
                     *[d for d in a.kw_defaults if d is not None],
                     *[x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg,
                                              a.kwarg] if x is not None and x.annotation]]
            if getattr(n, "returns", None) is not None:
                todo.append(n.returns)
            continue
        todo += list(ast.iter_child_nodes(n))
    return out


def import_time_reads(stmt: ast.stmt) -> Set[str]:
    return {x.id for x in import_time_nodes(stmt)
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}


def _root(x: ast.AST) -> Optional[str]:
    while isinstance(x, (ast.Attribute, ast.Subscript)):
        x = x.value
    return x.id if isinstance(x, ast.Name) else None


def changes(nodes: Iterable[ast.AST], names: Set[str]) -> List[str]:
    """What NODES change of NAMES' objects: a store into one (`X[k] = v`,
    `X.a = v`, `del X[k]`, `X[k] += v`), a method that changes it called
    on one (`X.append(v)`), a `global` statement."""
    out = []
    for x in nodes:
        if isinstance(x, (ast.Subscript, ast.Attribute)) and isinstance(x.ctx, (ast.Store, ast.Del)) \
                and _root(x) in names:
            out.append(f"{_root(x)} stored into")
        elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) \
                and x.func.attr in MUTATORS and _root(x.func.value) in names:
            out.append(f"{_root(x.func.value)}.{x.func.attr}()")
        elif isinstance(x, ast.Global):
            out.append(f"global {', '.join(x.names)}")
    return out


def _params(f: ast.AST) -> Set[str]:
    a = f.args  # type: ignore[attr-defined]
    return {x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if x}


def _literal(v: ast.AST) -> bool:
    """A value no call can change: a constant, or a tuple of them."""
    return isinstance(v, ast.Constant) or (isinstance(v, ast.Tuple)
                                           and all(_literal(e) for e in v.elts))


def constant_names(stmt: ast.stmt) -> Set[str]:
    """The names STMT binds to a value no call can change (`A = 'x'`,
    `A, B = 'x', 2`)."""
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
        return set()
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    out: Set[str] = set()
    for tg in targets:
        if isinstance(tg, ast.Name) and _literal(stmt.value):
            out.add(tg.id)
        elif isinstance(tg, ast.Tuple) and isinstance(stmt.value, ast.Tuple) \
                and len(tg.elts) == len(stmt.value.elts):
            out |= {e.id for e, v in zip(tg.elts, stmt.value.elts)
                    if isinstance(e, ast.Name) and _literal(v)}
    return out


def _changes_an_argument(stmt: ast.stmt) -> bool:
    """Whether STMT, a def or class of SOURCE, can change an object passed
    to it: a def's store into a parameter or a changing method called on
    one; a class's by its `__init__`."""
    if isinstance(stmt, ast.ClassDef):
        init = [b for b in stmt.body if isinstance(b, ast.FunctionDef) and b.name == "__init__"]
        return bool(init) and _changes_an_argument(init[0])
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return bool(changes(ast.walk(stmt), _params(stmt)))
    return False


def _called_at_import(nodes: List[ast.AST]) -> Set[str]:
    """The names NODES call: `f(...)`, and a decorator `@f` or `@f(...)`."""
    out = {x.func.id for x in nodes if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
    for x in nodes:
        for d in getattr(x, "decorator_list", ()):
            if isinstance(d, ast.Name):
                out.add(d.id)
    return out


def _runs_code(n: ast.stmt) -> bool:
    """N, at module level, runs more than imports, definitions and
    assignments (a call, a loop, a `with`, an augmented assignment); an
    `if` or `try` that only imports or assigns does not."""
    if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
                      ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Pass)):
        return False
    if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
        return False
    if isinstance(n, ast.If):
        return any(_runs_code(s) for s in [*n.body, *n.orelse])
    if isinstance(n, ast.Try):
        return any(_runs_code(s) for s in [*n.body, *n.orelse, *n.finalbody,
                                            *[x for h in n.handlers for x in h.body]])
    return True


def _name_targets(tree: ast.Module) -> Set[str]:
    """The names a module may build a patch target for in its own terms
    (TJ-3821): when it reads `__name__` or a `.__module__` (TJ-3871)
    outside its main guard, every `NAME` a string of it ends with `.NAME`
    (`f"{__name__}.NAME"`, `"%s.NAME" % __name__`, an alias of `__name__`,
    `f"{type(self).__module__}.NAME"`)."""
    body = [n for n in tree.body if not _is_main_guard(n)]
    if not any((isinstance(x, ast.Name) and x.id == "__name__")
               or (isinstance(x, ast.Attribute) and x.attr == "__module__")
               for n in body for x in ast.walk(n)):
        return set()
    return {m.group(1) for n in body for x in ast.walk(n)
            if isinstance(x, ast.Constant) and isinstance(x.value, str)
            for m in [re.search(r"\.(\w+)$", x.value)] if m}


def _calls_naming(tree: ast.AST, source: str, own: bool = False) -> List[Tuple[int, Set[str]]]:
    """The calls with SOURCE's name as an argument (`patch.multiple("SOURCE",
    X=...)`, `patch.object(import_module("SOURCE"), "X")`; in SOURCE,
    OWN, `__name__` too: TJ-3834): (line, the names their keywords and
    other string arguments give)."""
    out = []
    for x in ast.walk(tree):
        if isinstance(x, ast.Call):
            args = [*x.args, *(k.value for k in x.keywords)]
            inner = [a for a in ast.walk(x) if (isinstance(a, ast.Constant) and a.value == source)
                     or (own and isinstance(a, ast.Name) and a.id == "__name__")]
            if inner:
                named = {k.arg for k in x.keywords if k.arg} | {
                    a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
                out.append((x.lineno, named))
    return out


def _is_main_guard(n: ast.stmt) -> bool:
    return isinstance(n, ast.If) and ast.unparse(n.test).replace("'", '"') \
        == '__name__ == "__main__"'


# The repository's test modules.

def test_files(repo: Path) -> List[Path]:
    trees = [repo / "tests", *sorted((repo / "packages").glob("*/tests"))]
    return sorted(p for t in trees if t.is_dir() for p in t.rglob("*.py")
                  if "__pycache__" not in p.parts)


def read_table(text: str) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            raise Refused(f"the table's line {n}: `SOURCE SUPPORT DESCRIPTION`, such as "
                          f"`test_fix_x x what it holds`")
        if not re.fullmatch(r"test_\w+", parts[0]):
            raise Refused(f"the table's line {n}: the source {parts[0]} is no test module's "
                          f"name (test_*)")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", parts[1]) or parts[1].startswith("test"):
            raise Refused(f"the table's line {n}: the support module {parts[1]}: lower case, "
                          f"not test*")
        out.append((parts[0], parts[1], parts[2].rstrip(".")))
    for i, key in ((0, "source"), (1, "support")):
        seen = [r[i] for r in out]
        twice = sorted({s for s in seen if seen.count(s) > 1})
        if twice:
            raise Refused(f"the table names a {key} twice: {', '.join(twice)}")
    return out


def _alias(a: ast.alias) -> str:
    return a.name + (f" as {a.asname}" if a.asname else "")


def from_line(indent: str, module: str, names: Iterable[ast.alias], nl: str = "\n") -> str:
    """`from MODULE import a, b` at INDENT, wrapped in parentheses past
    WIDTH; no line end."""
    names = list(names)
    one = f"{indent}from {module} import {', '.join(_alias(a) for a in names)}"
    if len(one) <= WIDTH:
        return one
    out, line = [f"{indent}from {module} import ("], ""
    pad = indent + "    "
    for a in names:
        piece = _alias(a) + ","
        if line and len(pad) + len(line) + 1 + len(piece) > WIDTH:
            out.append(pad + line)
            line = ""
        line = f"{line} {piece}" if line else piece
    out += [pad + line, f"{indent})"]
    return nl.join(out)


def _line_end(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _string_hits(tree: ast.AST, source: str) -> List[Tuple[int, str]]:
    """The strings that name SOURCE's file (a path: `.../SOURCE.py`, or a
    bare `SOURCE.py` joined to one or passed to a call; one in a list of
    names is none) or something in it (a dotted target, `SOURCE.NAME`):
    (line, the string). No whitespace in it: not prose."""
    joined = set()
    for x in ast.walk(tree):
        if isinstance(x, ast.BinOp) and isinstance(x.op, ast.Div):
            joined |= {id(x.left), id(x.right)}
        elif isinstance(x, ast.Call):
            joined |= {id(a) for a in [*x.args, *(k.value for k in x.keywords)]}
    out = []
    for x in ast.walk(tree):
        if isinstance(x, ast.Constant) and isinstance(x.value, str) and not re.search(
                r"\s", x.value):
            v = x.value
            if v.startswith(source + ".") and v != f"{source}.py" or v.endswith(f"/{source}.py") \
                    or (v == f"{source}.py" and id(x) in joined):
                out.append((x.lineno, v))
    return out


def _is_path(v: str, source: str) -> bool:
    return v.endswith(f"/{source}.py") or v == f"{source}.py"


class Plan:
    """What extracting one source does, worked out before anything is written."""

    def __init__(self, repo: Path, source: str, support: str, about: str,
                 later: Iterable[str] = ()):
        self.repo, self.source, self.support, self.about = repo, source, support, about
        self.later = set(later)
        self.path = repo / "tests" / f"{source}.py"
        self.target = repo / SUPPORT / f"{support}.py"
        if not self.path.is_file() or self.path.is_symlink():
            raise Refused(f"{source}: no tests/{source}.py")
        if self.target.exists() or self.target.is_symlink():
            raise Refused(f"{source}: tests/_support/{support}.py exists")
        self.text = MT.read_exact(self.path)
        self.nl = _line_end(self.text)
        self.lines = MT.split_lines(self.text)
        self.tree = ast.parse(self.text)
        self._importers()
        self._closure()

    def _where(self, p: Path, line: int) -> str:
        return f"{p.relative_to(self.repo).as_posix()}:{line}"

    def _importers(self) -> None:
        self.wanted: Dict[str, Set[str]] = {}
        self.importers: Dict[Path, List[ast.ImportFrom]] = {}
        self.strings: List[Tuple[Path, int, str]] = []    # judged once the moved names are known
        self.calls: List[Tuple[Path, int, Set[str]]] = []
        own: Optional[int] = None                    # where SOURCE imports itself
        src = self.source
        for p in test_files(self.repo):
            tree = ast.parse(MT.read_exact(p))
            for line, v in _string_hits(tree, src):
                if _is_path(v, src):
                    raise Refused(f"{src}: {self._where(p, line)} names its file in a string "
                                  f"(a path: a loader or a scan)")
                self.strings.append((p, line, v))
            self.calls += [(p, line, named)
                           for line, named in _calls_naming(tree, src, p == self.path)]
            if p == self.path:
                own = min((n.lineno for n in ast.walk(tree) if (
                    isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == src) or (
                    isinstance(n, ast.Import) and any(a.name == src for a in n.names))),
                    default=None)
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == src:
                    if any(a.name == "*" for a in n.names):
                        raise Refused(f"{src}: {self._where(p, n.lineno)} star-imports it")
                    self.importers.setdefault(p, []).append(n)
                    for a in n.names:
                        self.wanted.setdefault(a.name, set()).add(self._where(p, n.lineno))
                elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[-1] == src:
                    raise Refused(f"{src}: {self._where(p, n.lineno)} imports it as "
                                  f"{'.' * n.level}{n.module or ''}")
                elif isinstance(n, ast.Import) and any(a.name.split(".")[-1] == src
                                                       for a in n.names):
                    raise Refused(f"{src}: {self._where(p, n.lineno)} imports the module "
                                  f"(its attribute reads: `from {src} import` them first)")
        if not self.wanted:
            raise Refused(f"{src}: no test module imports from it")
        if own is not None:
            raise Refused(f"{src}:{own} imports itself (a patch target in its own terms): by hand")

    def _closure(self) -> None:
        src, body = self.source, self.tree.body
        defs: Dict[str, ast.stmt] = {}
        count: Dict[str, int] = {}
        imports: Dict[str, List[Tuple[ast.stmt, ast.alias]]] = {}
        other: Dict[str, int] = {}          # bound in a compound statement, or by del
        for n in body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    imports.setdefault((a.asname or a.name).split(".")[0], []).append((n, a))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for name in module_binds(n):
                    count[name] = count.get(name, 0) + 1
                    defs.setdefault(name, n)
            else:
                for name in module_binds(n):
                    other.setdefault(name, n.lineno)
        declared = {g for x in ast.walk(self.tree) if isinstance(x, ast.Global) for g in x.names}
        classes = {n.name: n for n in body if isinstance(n, ast.ClassDef)}
        if "*" in imports:
            star = imports["*"][0][0]
            raise Refused(f"{src}:{star.lineno} star-imports {getattr(star, 'module', '')}: what "
                          f"the moved code reads from it is not known: by hand")

        missing = sorted(set(self.wanted) - set(defs))
        if missing:
            raise Refused(f"{src}: imported from it, but no module-level def, class or "
                          f"assignment of its (a re-export?): {', '.join(missing)}")

        def bound_twice(name: str) -> Optional[str]:
            if count.get(name, 0) > 1:
                return "twice"
            if name in imports:
                return "also by an import"
            if name in other:
                return f"also at line {other[name]}"
            if name in declared:
                return "also through `global`"
            return None

        moved: Set[str] = set()
        todo = sorted(self.wanted)
        need: Set[str] = set()
        while todo:
            name = todo.pop()
            if name in moved:
                continue
            why = bound_twice(name)
            if why:
                raise Refused(f"{src}: {name} is bound at module level {why}")
            moved.add(name)
            for r in sorted(module_reads(defs[name])):
                if r in defs and r not in moved:
                    todo.append(r)
                elif r in imports:
                    need.add(r)
                elif r in other:
                    raise Refused(f"{src}: {name} reads {r}, which line {other[r]} binds "
                                  f"(an if, try, for, with or del)")
        stmts = sorted({id(defs[n]): defs[n] for n in moved}.values(), key=lambda s: s.lineno)
        moved_ids = {id(s) for s in stmts}
        hooks = sorted(moved & HOOKS)
        if hooks:
            raise Refused(f"{src}: {', '.join(hooks)} would move: unittest's module hooks stay "
                          f"in the test module that runs them (by hand)")

        # The statements themselves.
        for s in stmts:
            runs = self._runs_tests(s, classes) if isinstance(s, ast.ClassDef) else None
            if runs:
                raise Refused(f"{src}: {s.name} is {runs}: they would run wherever it is "
                              f"imported (its tests to a mixin, by hand)")
            extra = module_binds(s) - moved
            if extra:
                raise Refused(f"{src}:{s.lineno} binds {', '.join(sorted(extra))} with "
                              f"{', '.join(sorted(module_binds(s) & moved))}: split it by hand")
            attrs = _place_attrs(s)         # (each said once: `__globals__`, not its label too)
            said = {GLOBALS_READS[a] for a in attrs if a in GLOBALS_READS}
            place = list(dict.fromkeys(
                sorted(module_reads(s) & PLACE) + attrs
                + [x for x in own_namespace(s, src, sys_names(imports, self.tree)) if x not in said]
                + location_reads(s, imports)))
            if place:
                raise Refused(f"{src}:{s.lineno} reads where it runs "
                              f"({', '.join(place)}): it would read the support module's")
        owner: Dict[int, int] = {}
        for n in body:
            first = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", ())])
            for ln in range(first, n.end_lineno + 1):
                if ln in owner and (owner[ln] in moved_ids or id(n) in moved_ids):
                    raise Refused(f"{src}:{ln} holds a statement that moves and another")
                owner[ln] = id(n)

        # The imports the moved names need.
        self.need_imports: List[Tuple[ast.stmt, ast.alias]] = []
        for r in sorted(need):
            for stmt, a in imports[r]:
                mod = stmt.module if isinstance(stmt, ast.ImportFrom) else a.name
                if isinstance(stmt, ast.ImportFrom) and stmt.level:
                    raise Refused(f"{src}:{stmt.lineno} the names need a relative import")
                parts = (mod or "").split(".")
                if parts[0].startswith("test_") or parts[-1].startswith("test_"):
                    if mod in self.later:
                        raise Refused(f"{src}: the names need {r} from {mod}", needs=mod)
                    raise Refused(f"{src}: the names need {r} from the test module {mod}: "
                                  f"extract that first")
                self.need_imports.append((stmt, a))
        self.need_imports.sort(key=lambda sa: (sa[0].lineno, sa[0].col_offset))

        self.moved, self.stmts = moved, stmts
        rest = [n for n in body if id(n) not in moved_ids]
        reads_back: Set[str] = set()
        for n in rest:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                continue
            if _is_main_guard(n):
                reads_back |= module_reads(n)
                continue
            how = own_namespace(n, src, sys_names(imports, self.tree))
            if how:
                raise Refused(f"{src}:{n.lineno} reads its own module's names by name "
                              f"({how[0]}): by hand")
            if _runs_code(n):
                raise Refused(f"{src}:{n.lineno} runs code at import ({type(n).__name__}): "
                              f"an importer of the moved names would no longer run it: by hand")
            self._at_import(n, defs, imports)
            reads_back |= module_reads(n)
        self.uses_back = sorted(moved & reads_back, key=lambda n: (defs[n].lineno, n))
        self.import_end = self._import_end()
        for name in sorted(_name_targets(self.tree) & moved):
            raise Refused(f"{src}: it reads `__name__` (or a `.__module__`) and a string of it "
                          f"ends with `.{name}`, "
                          f"which moves (a patch target in its own terms): by hand")
        for p, line, named in self.calls:
            for name in sorted(named & moved):
                raise Refused(f"{src}: {self._where(p, line)} names it and {name} in one call, "
                              f"and {name} moves (a patch target): by hand")
        for p, line, v in self.strings:
            hit = v[len(src) + 1:].split(".")[0]
            if hit in moved:
                raise Refused(f"{src}: {self._where(p, line)} names {v} in a string, and "
                              f"{hit} moves (a patch target would patch SOURCE's name, not "
                              f"the one the moved code reads): by hand")
        for s in stmts:
            if s.lineno <= self.import_end:
                raise Refused(f"{src}:{s.lineno} a name that moves is bound among its imports")

    def _at_import(self, n: ast.stmt, defs: Dict[str, ast.stmt],
                   imports: Dict[str, list]) -> None:
        """N stays in SOURCE and runs at its import: what an importer of the
        moved names would no longer have run, or would read another way, is
        refused (TJ-3790, TJ-3794). Reading a moved name, or calling a
        helper that changes nothing, is the same in either place."""
        src, moved = self.source, self.moved
        nodes = import_time_nodes(n)
        touched = changes(nodes, moved | set(imports))
        if touched:
            raise Refused(f"{src}:{n.lineno} changes at import what an importer of the moved "
                          f"names would read ({touched[0]}): it would no longer run there "
                          f"(by hand)")
        for call in (x for x in nodes if isinstance(x, ast.Call)):
            passed = sorted(m for m in {_root(v) for v in [*call.args,
                                                           *(k.value for k in call.keywords)]}
                            & moved if m not in constant_names(defs[m]))
            callee = _root(call.func)
            if not passed or callee is None:
                continue
            if callee in defs:
                if not isinstance(defs[callee], (ast.FunctionDef, ast.AsyncFunctionDef,
                                                 ast.ClassDef)) \
                        or not _changes_an_argument(defs[callee]):
                    continue      # a method of SOURCE's object (changes(), above), or no change
            elif callee not in imports:
                continue          # a builtin
            raise Refused(f"{src}:{n.lineno} passes {', '.join(passed)} at import to {callee}, "
                          f"which can change it: an importer of the moved names would no "
                          f"longer run it (by hand)")
        for f in sorted(_called_at_import(nodes) & set(defs)):
            how = self._changes_of(f, defs, imports)
            if how:
                raise Refused(f"{src}:{n.lineno} calls {f} at import, which changes module-level "
                              f"state ({how}): an importer of the moved names would no longer "
                              f"run it (by hand)")
        early = sorted(m for m in import_time_reads(n) & moved if n.lineno < defs[m].lineno)
        if early:
            raise Refused(f"{src}:{n.lineno} reads {', '.join(early)} at import above its "
                          f"definition: it read another there before (a builtin?): by hand")

    def _changes_of(self, name: str, defs: Dict[str, ast.stmt], imports: Dict[str, list],
                    seen: Tuple[str, ...] = ()) -> Optional[str]:
        """What calling NAME, a def or class of SOURCE, changes of the
        module-level objects it reads (its own module's or an imported
        one's), through the defs and classes it calls too."""
        stmt = defs[name]
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return None
        reads = module_reads(stmt)
        found = changes(ast.walk(stmt), reads & (set(defs) | set(imports)))
        if found:
            return found[0]
        for r in sorted(reads & set(defs)):
            if r != name and r not in seen:
                how = self._changes_of(r, defs, imports, seen + (name,))
                if how:
                    return f"{how}, through {r}"
        return None

    @staticmethod
    def _runs_tests(c: ast.ClassDef, classes: Dict[str, ast.ClassDef]) -> Optional[str]:
        """Why C would run tests wherever it is bound (TJ-3946), or None: a
        TestCase (a base named *TestCase, of its own or of a class of
        SOURCE's it builds on) with tests (a `test*` or `runTest` method or
        attribute of those classes) or with a base from elsewhere (whose
        tests are not read here); or a class with tests on a base from
        elsewhere (a TestCase not so named, as a fixture base moved to
        tests/_support/ is). A fixture base (a TestCase with setUp,
        setUpClass or helpers, no tests) or a mixin moves as it is."""
        case = tests = other = False
        todo, seen = [c], {c.name}
        while todo:
            k = todo.pop()
            own = [n.name for n in k.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            own += [t.id for n in k.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                    for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                    if isinstance(t, ast.Name)]
            tests = tests or any(n.startswith("test") or n == "runTest" for n in own)
            for b in k.bases:
                s = ast.unparse(b)
                if s.split(".")[-1].endswith("TestCase"):
                    case = True
                elif s in classes:
                    if s not in seen:
                        seen.add(s)
                        todo.append(classes[s])
                elif s != "object":
                    other = True
        if case and (tests or other):
            return "a TestCase class " + ("with tests" if tests else
                                          "on a base from elsewhere (its tests unread here)")
        return "a class with tests on a base from elsewhere" if tests and other else None

    def _import_end(self) -> int:
        """The last line of SOURCE's leading imports (before its first
        other statement but its docstring), or of the docstring."""
        return _import_end(self.tree)

    def _span(self, n: ast.stmt) -> Tuple[int, int]:
        """N's 0-based first and last lines: its decorators and the comment
        lines directly above it with it (below the imports)."""
        first = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", ())]) - 1
        while first - 1 >= self.import_end and self.lines[first - 1].lstrip().startswith("#") \
                and not BANNER.match(self.lines[first - 1].lstrip()):
            first -= 1
        return first, n.end_lineno - 1

    # The texts.
    def support_text(self) -> str:
        nl = self.nl
        out = [f'"""{HEADLINE}{self.about}.{nl}{nl}'
               f'{MARK}{self.source}{nl}"""{nl}']
        future = [n for n in self.tree.body if isinstance(n, ast.ImportFrom)
                  and n.module == "__future__"]
        out += [f"from __future__ import {', '.join(_alias(a) for a in n.names)}{nl}"
                for n in future]
        groups: List[Tuple[ast.stmt, List[ast.alias]]] = []
        for stmt, a in self.need_imports:
            if groups and groups[-1][0] is stmt:
                groups[-1][1].append(a)
            else:
                groups.append((stmt, [a]))
        prev_end = 0
        for stmt, names in groups:
            if prev_end and any(not ln.strip() for ln in self.lines[prev_end:stmt.lineno - 1]):
                out.append(nl)             # the source's blank line between import groups
            prev_end = stmt.end_lineno
            if isinstance(stmt, ast.Import):
                out += [f"import {_alias(a)}{nl}" for a in names]
            else:
                out.append(from_line("", stmt.module or "", names, nl) + nl)
        for i, s in enumerate(self.stmts):
            first, last = self._span(s)
            blanks = 0                      # as many blank lines above it as SOURCE has
            while blanks < first and not self.lines[first - 1 - blanks].strip():
                blanks += 1
            out.append(nl * (2 if i == 0 else min(2, blanks)))
            out.append("".join(self.lines[first:last + 1]).rstrip("\r\n") + nl)
        return "".join(out)

    def source_text(self) -> str:
        nl = self.nl
        cut = object()
        drop: Set[int] = set()
        for s in self.stmts:
            first, last = self._span(s)
            drop.update(range(first, last + 1))
        marked: list = []
        for i, ln in enumerate(self.lines):
            if i in drop:
                if not marked or marked[-1] is not cut:
                    marked.append(cut)
            else:
                marked.append(ln)
        text = "".join(_close_cuts(marked, cut, nl))
        before = _loads(self.tree)
        tree = ast.parse(text)
        after = _loads(tree)
        lines = MT.split_lines(text)
        insert = _import_end(tree)
        edits: List[Tuple[int, int, List[str]]] = []
        helpers: Set[int] = set()   # where an import of a test-tree helper was dropped
        tests = self.path.parent
        for n in tree.body:
            if not isinstance(n, (ast.Import, ast.ImportFrom)) or n.lineno > insert \
                    or "noqa" in lines[n.end_lineno - 1]:
                continue
            gone = [a for a in n.names if (a.asname or a.name).split(".")[0] in before
                    and (a.asname or a.name).split(".")[0] not in after]
            if not gone:
                continue
            left = [a for a in n.names if a not in gone]
            for a in gone:
                top = (a.name if isinstance(n, ast.Import) else
                       "" if n.level else (n.module or "")).split(".")[0]
                if top and ((tests / f"{top}.py").is_file()
                            or (tests / top / "__init__.py").is_file()):
                    helpers.add(n.lineno - 1)
            if not left:
                new: List[str] = []
            elif isinstance(n, ast.Import):
                new = [f"import {', '.join(_alias(a) for a in left)}"]
            else:
                new = [from_line("", "." * n.level + (n.module or ""), left, nl)]
            edits.append((n.lineno - 1, n.end_lineno - 1, new))
        for first, last, new in sorted(edits, reverse=True):
            lines[first:last + 1] = [x + nl for x in new]
            if insert - 1 >= last:
                insert -= (last - first + 1) - len(new)
        # Where the first dropped helper's import was, so what it ran (the
        # hermetic setup) runs at the same point (TJ-3845); else last.
        if helpers:
            first, _last, new = min(e for e in edits if e[0] in helpers)
            # (the drops above it have shortened what comes before it)
            insert = first + len(new) - sum((b - a + 1) - len(n) for a, b, n in edits if b < first)
        if self.uses_back:
            back = from_line("", f"_support.{self.support}",
                             [ast.alias(name=n) for n in self.uses_back], nl)
            lines.insert(insert, back + nl)
        elif helpers and any((tests / h).exists() for h in ("_hermetic", "_hermetic.py")):
            lines.insert(insert, "import _support  # noqa: F401  (the hermetic setup, as the "
                                 "dropped imports gave it)" + nl)
        return "".join(lines)

    def importer_text(self, p: Path) -> str:
        text = MT.read_exact(p)
        nl = _line_end(text)
        lines = MT.split_lines(text)
        for n in sorted(self.importers[p], key=lambda n: n.lineno, reverse=True):
            first, last = n.lineno - 1, n.end_lineno - 1
            indent = lines[first][:n.col_offset]
            if indent.strip():
                raise Refused(f"{self.source}: {self._where(p, n.lineno)} shares its line")
            tail = lines[last][n.end_col_offset:]
            new = from_line(indent, f"_support.{self.support}", n.names, nl)
            lines[first:last + 1] = [new + (tail if tail.strip() else nl)]
        return "".join(lines)

    def texts(self) -> Dict[Path, str]:
        out = {self.target: self.support_text(), self.path: self.source_text()}
        for p in self.importers:
            out[p] = self.importer_text(p)
        for p, t in out.items():
            try:
                ast.parse(t)
            except SyntaxError as e:
                raise Refused(f"{self.source}: the new {p.name} does not parse ({e.msg}, "
                              f"line {e.lineno}): a bug of the tool's") from e
        return out


def _loads(tree: ast.AST) -> Set[str]:
    return {x.id for x in ast.walk(tree) if isinstance(x, ast.Name)
            and isinstance(x.ctx, ast.Load)}


def _import_end(tree: ast.Module) -> int:
    end = 0
    for i, n in enumerate(tree.body):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            end = n.end_lineno
        elif i == 0 and isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            end = n.end_lineno
        else:
            break
    return end


def _close_cuts(marked: list, cut: object, nl: str) -> List[str]:
    """MARKED's lines with each CUT (where statements left) gone, and the
    blank lines around it at most two; none at the end. Cuts with only
    blank lines between them count as one. A cut directly below a comment
    (a section's banner) takes the blank lines below it along, when code
    follows them: the banner stays on what stays (TJ-3826, TJ-3839)."""
    out: List[str] = []
    i = 0
    while i < len(marked):
        x = marked[i]
        if x is not cut:
            out.append(x)
            i += 1
            continue
        before = 0
        while out and not out[-1].strip():
            out.pop()
            before += 1
        i += 1
        while True:               # cuts with only blank lines between them are one (TJ-3839)
            after = 0
            while i < len(marked) and marked[i] is not cut and not marked[i].strip():
                after += 1
                i += 1
            if i < len(marked) and marked[i] is cut:
                i += 1
                continue
            break
        if i < len(marked):
            below = not marked[i].lstrip().startswith("#")
            if before == 0 and out and out[-1].lstrip().startswith("#") and below:
                continue
            out += [nl] * min(2, before + after)
    while out and not out[-1].strip():
        out.pop()
    if out and not out[-1].endswith(("\n", "\r")):
        out[-1] += nl
    return out


def cross_import_count(repo: Path) -> int:
    """tests/test_test_layout.py's count of test modules that import, or
    load, a test module (its cross_imports)."""
    spec = importlib.util.spec_from_file_location("_extract_layout", repo / LAYOUT)
    mod = importlib.util.module_from_spec(spec)
    tests = str(repo / "tests")
    sys.path.insert(0, tests)
    saved, bytecode = set(sys.modules), sys.dont_write_bytecode
    sys.dont_write_bytecode = True         # no __pycache__ the batch would carry
    try:
        spec.loader.exec_module(mod)
        files = test_files(repo)
        stems = {p.stem for p in files if p.name.startswith("test_")}
        return sum(1 for p in files if mod.cross_imports(p, stems))
    finally:
        sys.dont_write_bytecode = bytecode
        sys.path.remove(tests)
        for k in set(sys.modules) - saved:
            del sys.modules[k]


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    MT.write_exact(p, text)


def extract(repo: Path, table: List[Tuple[str, str, str]], dry_run: bool = False) -> int:
    pending = sorted(table)
    for source, support, _about in pending:
        if (repo / SUPPORT / f"{support}.py").exists():
            raise Refused(f"{source}: tests/_support/{support}.py exists")
    done: List[Plan] = []
    while pending:
        first: Optional[Refused] = None
        for row in pending:
            later = {r[0] for r in pending if r is not row}
            try:
                plan = Plan(repo, *row, later=later)
            except Refused as e:
                if e.needs is None or dry_run:
                    raise
                first = first or e
                continue
            if not dry_run:
                texts = plan.texts()
                init = repo / SUPPORT / "__init__.py"
                if not init.exists():
                    write(init, INIT_DOC + (INIT_HERMETIC if any(
                        (repo / "tests" / h).exists() for h in ("_hermetic", "_hermetic.py"))
                        else ""))
                for p, t in texts.items():
                    write(p, t)
            done.append(plan)
            pending.remove(row)
            break
        else:
            assert first is not None
            raise Refused(f"{first} (each source left needs another's names first)")
    for p in done:
        print(f"extract_support: {p.source} -> tests/_support/{p.support}.py: "
              f"{len(p.moved)} name(s) in {len(p.stmts)} statement(s), {len(p.importers)} "
              f"importing module(s); {p.source} imports back {len(p.uses_back)}")
    if not dry_run:
        n = cross_import_count(repo)
        lay = repo / LAYOUT
        text = MT.read_exact(lay)
        new = re.sub(r"^CROSS_IMPORTS_MAX = \d+", f"CROSS_IMPORTS_MAX = {n}", text, count=1,
                     flags=re.M)
        if new != text:
            write(lay, new)
        print(f"extract_support: CROSS_IMPORTS_MAX = {n}")
    return 0


# --check

def _vt():
    return _load("verify_test_moves")


def table_of(base: Path, head: Path) -> List[Tuple[str, str, str]]:
    """The table HEAD's new support modules name."""
    out = []
    d = head / SUPPORT
    for p in sorted(d.glob("*.py")) if d.is_dir() else []:
        if p.name == "__init__.py" or (base / SUPPORT / p.name).exists():
            continue
        doc = ast.get_docstring(ast.parse(MT.read_exact(p)), clean=False) or ""
        lines = doc.splitlines()
        src = [ln[len(MARK):] for ln in lines if ln.startswith(MARK)]
        if len(src) != 1 or not lines or not lines[0].startswith(HEADLINE) \
                or not lines[0].endswith("."):
            raise Refused(f"tests/_support/{p.name}: no `{HEADLINE}...` first line or not one "
                          f"`{MARK}SOURCE` line: not the tool's")
        out.append((src[0], p.stem, lines[0][len(HEADLINE):-1]))
    return out


Entries = Dict[str, Tuple[str, str]]        # a path -> (its mode, its object id)


def _git(repo: Path, *a: str, data: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", *a], cwd=repo, input=data, capture_output=True, check=True,
                          env=_vt()._genv()).stdout


def tree_entries(repo: Path, rev: str) -> Entries:
    """Every path of REV's tree, with its mode and object: what git holds,
    not what `git archive` shows (a `.gitattributes` export-ignore or a
    conversion applies there)."""
    out: Entries = {}
    for rec in _git(repo, "ls-tree", "-r", "-z", "--full-tree", rev).split(b"\0"):
        if rec:
            meta, path = rec.split(b"\t", 1)
            mode, _kind, oid = meta.decode("ascii").split()
            f = path.decode("utf-8", "surrogateescape")
            if f in out:
                raise Refused(f"{f!r}: twice in the tree (git fsck's duplicateEntries)")
            out[f] = (mode, oid)
    return out


def materialize(repo: Path, rev: str, dest: Path) -> Entries:
    """REV's files under DEST, each blob's bytes as git holds them; its
    entries. A symlink, a submodule or a path that leaves DEST is refused."""
    entries = tree_entries(repo, rev)
    for f, (mode, _o) in sorted(entries.items()):
        parts = Path(f).parts
        if mode not in ("100644", "100755"):
            raise Refused(f"{f}: a symlink or a submodule (mode {mode})")
        if f.startswith("/") or ".." in parts or ".git" in parts or "\n" in f:
            raise Refused(f"{f!r}: not a path inside the tree")
    oids = sorted({o for _m, o in entries.values()})
    raw = _git(repo, "cat-file", "--batch", data="".join(f"{o}\n" for o in oids).encode())
    blobs: Dict[str, bytes] = {}
    i = 0
    for o in oids:
        nl = raw.index(b"\n", i)
        size = int(raw[i:nl].split()[2])
        blobs[o] = raw[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    for f, (mode, o) in entries.items():
        q = dest / f
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(blobs[o])
        if mode == "100755":
            q.chmod(0o755)
    return entries


def work_entries(repo: Path, tree: Path) -> Entries:
    """TREE's files as git would hold them: each one's mode and object id
    (its bytes hashed, no filter); a symlink is mode 120000."""
    files = sorted(q for q in tree.rglob("*") if q.is_file() or q.is_symlink())
    out: Entries = {}
    plain = [q for q in files if not q.is_symlink()]
    for q in files:
        if q.is_symlink():
            out[q.relative_to(tree).as_posix()] = ("120000", os.readlink(q))
    oids = _git(repo, "hash-object", "--no-filters", "--stdin-paths",
                data="".join(f"{q}\n" for q in plain).encode()).decode().split()
    for q, o in zip(plain, oids):
        mode = "100755" if q.stat().st_mode & 0o111 else "100644"
        out[q.relative_to(tree).as_posix()] = (mode, o)
    return out


# What importing a module leaves, read in a fresh interpreter from the
# tree's tests/ folder, the hermetic setup loaded first as the runner and
# discovery load it (TJ-3816, TJ-3817, TJ-3829 to TJ-3831): the value of
# each name asked for; the environment as C holds it; sys.path's entries,
# and which one gives each name more than one gives; the decimal
# context; the random state (seeded first); the fake modules in
# sys.modules; a digest of each name of the repository's other modules
# then loaded; and, of the repository's modules a run of BASE loaded and
# this one did not, what importing each (after its own imports) changes
# of all that. Run only on BASE and on a HEAD the text check has passed.
# _SNAP is the walk, on its own for the tests.
_SNAP = r'''
import abc, ast, collections, ctypes, datetime, decimal, enum, fractions, functools, gc, hashlib
import importlib, importlib.util, json, os, pathlib, pickle, random, re, sys, types
root = real = "/nonexistent-root"
SUBS = []
# The product's memos that another module's import fills (TJ-3842,
# TJ-3870): a dropped import that changes one changes nothing an importer
# reads. Each is keyed by what it was computed from.
MEMOS = {
    "taxjson.lib.once._SETS": "each module's warn-once set, registered at its import: a "
                              "module not loaded says nothing",
    "taxjson.lib.markets_data._DATA": "the markets data, read on first use",
    "taxjson.lib.brokerages.detect._MATCH_CACHE": "a file's sibling matches, by its path, "
                                                  "mtime and size",
    "taxjson.lib.markets._OVR_CACHE": "a ticker map's overrides, by its path and mtime",
    "taxjson.lib.markets_data._SUFFIX_RE_CACHE": "the listing-suffix pattern, by its "
                                                 "suffixes",
    "taxjson.lib.project_layout._SETTINGS_CACHE": "a project's settings, by its path and "
                                                  "file stamp",
    "taxjson.lib.tobase_map._OVERLAY_CACHE": "a rates overlay, by its inputs",
    "taxjson.lib.fetchers._CACHE": "the fetchers installed, found on first use",
}
# The product's cache wrappers (functools.lru_cache, cache: TJ-3965), each of
# a function of its arguments and the shipped files: their entries are left
# out wherever a wrapper is held, but the wrapper itself is walked, so a store
# that replaces one is seen (TJ-3975).
CACHED = {
    "taxjson.project.settings._keys_known": "a table class's keys, by the class",
    "taxjson.lib.market_calendar.nyse_holidays": "a year's NYSE holidays, by the year",
    "taxjson.lib.market_calendar.fed_holidays": "a year's Federal Reserve holidays, by the year",
    "taxjson.lib.market_calendar.tsx_holidays": "a year's TSX holidays, by the year",
    "taxjson.lib.market_calendar.cds_holidays": "a year's CDS holidays, by the year",
    "taxjson.lib.tx_load._tx_field_names": "a transaction's keywords, from its signature",
    "taxjson.bin.taxjson_redact._generated_comments": "the comment lines taxjson writes, from "
                                                      "its templates",
    "taxjson.lib.tobase_map._load": "a rates map, by its path and file stamp",
    "taxjson.lib.tobase_map._receipt_pairs": "a map's receipt pairs, by its path and file stamp",
    "taxjson.lib.markets_data.known_suffixes": "the listing suffixes, from the markets data",
    "taxjson.lib.brokerages.generic._cached_choice": "a file's mapping, by its path, its "
                                                     "files' state and the candidates",
    "taxjson.lib.pool_eps._is_coin_symbol": "a symbol's kind, by the symbol",
    "taxjson.lib.markets._list": "a named list of the markets data, by its name",
    "taxjson.lib.markets.receipt_suffixes": "the receipt venues' suffixes, from the markets data",
    "taxjson.lib.markets_data.canadian_suffixes": "the Canadian venues' suffixes, from the "
                                                  "markets data",
    "taxjson.engine.rows._is_iso_date": "a string's form, by the string",
    "taxjson.engine.rows._iso_ordinal": "a date's ordinal, by its text",
    "taxjson.engine.ca.run_helpers._futures_names": "lib/futures' two names, imported once",
    "taxjson.lib.cra_slips.issuer_aliases": "the shipped issuer aliases, from their file",
    "taxjson.lib.option_symbols._occ_match_text": "a symbol's OCC match, by its spelling",
    "taxjson.lib.option_symbols._expiry_iso": "an expiry's date, by its six digits",
}
MEMOS.update(CACHED)
ADDR = re.compile(r" at 0x[0-9a-fA-F]+")
VALUE = (datetime.date, datetime.time, datetime.timedelta, decimal.Decimal, fractions.Fraction,
         pathlib.PurePath, range, enum.Enum, re.Pattern)
TYPING = tuple(x for x in (getattr(types, "GenericAlias", None), getattr(types, "UnionType", None))
               if x)
# functools' cache wrapper (lru_cache, cache; on a method too): pickled by
# reference, its entries are its own (TJ-3965).
CACHE = type(functools.lru_cache(maxsize=None)(lambda: None))
# A class's own dunders that say where it was defined, not what it holds;
# and copyreg's record of its slot names, written there the first time one
# of its objects is reduced (by the walk too: TJ-3910).
PLACE_DUNDERS = {"__module__", "__qualname__", "__dict__", "__weakref__", "__firstlineno__",
                 "__static_attributes__", "__slotnames__"}
DEPTH, BUDGET = 12, 500000
walk = {"left": 0, "cut": False}
# Stand-ins the walk reads as what they stand for, by id (the state child's
# watched os.stat and os.lstat, which a module bound under them keeps).
ALIASES = {}


def norm(s):
    for v, k in SUBS:
        s = s.replace(v, k)
    return ADDR.sub(" at 0x?", s.replace(real, "<ROOT>").replace(root, "<ROOT>"))


def full(c):
    try:
        c.cell_contents
        return True
    except ValueError:
        return False


def mine(f):
    return os.path.realpath(f or "/").startswith(real + os.sep)


def outside(t):
    """T's class is the standard library's or a package's, not the
    repository's: its pickle names the same module in either tree."""
    return not mine(getattr(sys.modules.get(t.__module__), "__file__", None))


def fake(v):
    """A module no import made: no spec, or a bare one (no loader, no
    origin, no search path: `module_from_spec(ModuleSpec(name, None))`,
    TJ-3867). Its names are its state."""
    if not isinstance(v, types.ModuleType):
        return False
    s = getattr(v, "__spec__", None)
    return s is None or (getattr(s, "loader", None) is None and getattr(s, "origin", None) is None
                         and not getattr(s, "submodule_search_locations", None))


def opaque(v, t):
    """What else V holds: its repr, unless that is object's (an address
    only), then its pickle; else only its type."""
    r = repr(v)
    if r != object.__repr__(v):
        return ["opaque", t.__qualname__, norm(r)]
    try:
        return ["opaque", t.__qualname__, hashlib.sha1(pickle.dumps(v, 4)).hexdigest()]
    except Exception:
        return ["opaque", t.__qualname__]


def slots(v, t):
    out = []
    for c in t.__mro__:
        s = getattr(c, "__slots__", ())
        out += [x for x in ((s,) if isinstance(s, str) else s)
                if x not in ("__dict__", "__weakref__") and hasattr(v, x)]
    return out


def bare(f):
    """F is a plain function without parameters: a cache of it holds at
    most one entry, the one a call without arguments finds (TJ-3977)."""
    if not isinstance(f, types.FunctionType):
        return False
    c = f.__code__
    return not (c.co_argcount or c.co_kwonlyargcount or c.co_flags & 0x0C)   # *a, **k


def own_function(v):
    """The function cache wrapper V calls, as its C type holds it, not
    its `__wrapped__`, which a store or `update_wrapper` can replace
    (TJ-3992): what V refers to where a probe wrapper of its kind, holding
    one entry as V does, refers to its own; None when the two differ."""
    def probe():
        return None
    try:
        p = functools.lru_cache(**v.cache_parameters())(probe)
        p()
        ours, its = gc.get_referents(p), gc.get_referents(v)
    except Exception:
        return None
    at = [i for i, r in enumerate(ours) if r is probe]
    return its[at[0]] if len(at) == 1 and len(its) == len(ours) else None


def deep(v, depth=0, seen=frozenset()):
    """V as JSON-able lists: values by repr, containers by their items,
    functions by name, defaults, closure and attributes, a generator by
    its frame's locals and place and what it delegates to (`yield from`),
    the live environment by name (a function's default),
    classes by name, bases and attributes
    (the dunders too, but where it was defined; one inside another value
    that is a module's own name, by that name only: it is walked there),
    objects by type, attributes and slots (and, for a class outside the
    repository, its repr or pickle; for a class of the repository's with
    an outside base, its reduce), a decimal context by its settings (not
    its flags), a cache wrapper (`functools.lru_cache`, `cache`) by what
    it wraps, its attributes but where it was defined (TJ-4013) and how
    many entries it holds (one of a function without
    parameters by its value too: TJ-3977; the function it holds,
    `own_function`: TJ-3992, and when that is not found, cut short:
    TJ-4005), but a memo MEMOS names, by
    what it wraps (TJ-3965), a fake module by its names, others by repr
    or pickle.
    Past DEPTH levels or BUDGET values it is cut short, and walk["cut"]
    says so; so it does for a value it would see by its type alone, with
    no attributes or slots of its own to walk (an itertools.cycle on
    Python 3.14, a SimpleQueue) or of a repository class whose outside
    base keeps its state from its reduce and its pickle (`class
    Q(queue.SimpleQueue)`) (TJ-3875, TJ-3906)."""
    walk["left"] -= 1
    if walk["left"] < 0 or depth > DEPTH:
        walk["cut"] = True
        return "<deep>"
    v = ALIASES.get(id(v), v)
    t = type(v)
    if v is None or t in (bool, int, float, complex, str, bytes) or isinstance(v, VALUE) \
            or t.__module__ == "typing" or isinstance(v, TYPING):   # an annotation: its repr
        return [t.__qualname__, norm(repr(v))]
    if id(v) in seen:
        return "<cycle>"
    seen = seen | {id(v)}

    def d(x):
        return deep(x, depth + 1, seen)
    if v is os.environ or v is getattr(os, "environb", None):   # the live one (a default)
        return ["environ"]
    if isinstance(v, types.ModuleType):
        return ["module", v.__name__, d(dict(vars(v))) if fake(v) else None]
    if isinstance(v, (list, tuple, collections.deque)):
        return [t.__qualname__, [d(e) for e in v], d(getattr(v, "__dict__", None))]
    if isinstance(v, (set, frozenset)):
        return [t.__qualname__, sorted(json.dumps(d(e)) for e in v)]
    if isinstance(v, dict):
        return [t.__qualname__, [[d(k), d(x)] for k, x in v.items()],
                d(getattr(v, "default_factory", None)), d(getattr(v, "__dict__", None))]
    if isinstance(v, functools.partial):
        return ["partial", d(v.func), d(v.args), d(v.keywords)]
    if isinstance(v, types.GeneratorType):          # its frame and its delegate (TJ-3844)
        f = v.gi_frame
        return ["generator", v.__qualname__, d(dict(f.f_locals)) if f else None,
                f.f_lasti if f else -1, d(v.gi_yieldfrom)]   # (`yield from`'s: TJ-3865)
    if isinstance(v, decimal.Context):              # its settings; its flags only record
        return ["Context", v.prec, str(v.rounding), v.Emin, v.Emax, v.capitals, v.clamp,
                sorted(k.__name__ for k, on in v.traps.items() if on)]
    if isinstance(v, types.FunctionType):
        return ["function", v.__qualname__, d(v.__defaults__), d(v.__kwdefaults__),
                [d(c.cell_contents) for c in v.__closure__ or () if full(c)], d(dict(vars(v)))]
    if isinstance(v, CACHE):                        # what it wraps, how many it holds
        held = one = None                           # (a named memo: not that)
        if f"{getattr(v, '__module__', '')}.{getattr(v, '__qualname__', '')}" not in MEMOS:
            try:
                held = v.cache_info().currsize
            except Exception:
                held = None
            fn = own_function(v) if held == 1 else None
            if held == 1 and fn is None:            # its one entry unseen (TJ-4005)
                walk["cut"] = True
            elif bare(fn):
                try:                                # its one entry, by a call the
                    one = d(v())                    # cache answers (TJ-3977)
                except Exception as e:
                    one = ["<call failed>", type(e).__name__]
        return ["cache", d({k: x for k, x in vars(v).items()     # (where it was defined:
                            if k not in PLACE_DUNDERS}), held, one]  # update_wrapper's, TJ-4013)
    if isinstance(v, (classmethod, staticmethod)):
        return [t.__qualname__, d(v.__func__)]
    if isinstance(v, property):
        return ["property", d(v.fget), d(v.fset), d(v.fdel)]
    if isinstance(v, (types.MethodType, types.BuiltinFunctionType, types.MethodWrapperType)):
        own = getattr(v, "__self__", None)
        return ["method", getattr(v, "__qualname__", t.__qualname__),
                None if isinstance(own, types.ModuleType) and not fake(own) else d(own)]
    if isinstance(v, type) and depth and getattr(sys.modules.get(v.__module__), v.__qualname__,
                                                 None) is v:    # a module's own name (TJ-3843)
        return ["class", v.__qualname__]
    if isinstance(v, type):
        return ["class", v.__qualname__, [b.__qualname__ for b in v.__mro__[1:]],
                [[k, abc_registry(v) if k == "_abc_impl" else d(x)]
                 for k, x in vars(v).items() if k not in PLACE_DUNDERS]]
    s = slots(v, t)
    if hasattr(v, "__dict__") or s:
        if outside(t):          # what C holds (a Random's state); by its type alone, the
            held = opaque(v, t)  # walk of its attributes is the view (a cached_property)
        elif any(outside(b) for b in t.__mro__[1:-1]):
            held = reduced(v, d) or opaque(v, t)    # an outside base's (TJ-3866)
            if held == ["opaque", t.__qualname__]:  # its type alone: unseen (TJ-3906)
                walk["cut"] = True
        else:
            held = None
        return ["object", t.__qualname__, d(dict(getattr(v, "__dict__", {}))),
                [[x, d(getattr(v, x))] for x in s], held]
    o = opaque(v, t)
    if len(o) == 2:         # neither its repr nor its pickle reads it: unseen (TJ-3875)
        walk["cut"] = True
    return o


def abc_registry(cls):
    """An abstract class's registered virtual subclasses, by name: what
    its `_abc_impl` holds but its caches (memos of isinstance)."""
    try:
        reg = abc._get_dump(cls)[0]
    except Exception:
        return None
    return sorted(f"{c.__module__}.{c.__qualname__}" for c in (r() for r in reg) if c is not None)


def reduced(v, d):
    """V's reduce, deep (the arguments and the state): what an outside base
    holds for an object of a class of the repository's (`class
    _Rng(random.Random)`, a bytearray's bytes). None when it has none (a C
    base that keeps its state to itself: `class Q(queue.SimpleQueue)`)."""
    try:
        r = v.__reduce_ex__(4)
    except Exception:
        return None
    return ["reduce", d(list(r[1:3])) if isinstance(r, tuple) else d(r)]


def snap(v):
    walk["left"], walk["cut"] = BUDGET, False
    return json.dumps(deep(v))
'''
_STATE_DEFS = r'''
import logging, select, shutil, signal, tempfile, time, warnings


def c_environ():
    """The process's environment as C holds it (os.putenv's too)."""
    try:
        env = ctypes.POINTER(ctypes.c_char_p).in_dll(ctypes.CDLL(None), "environ")
        got, i = [], 0
        while env[i]:
            got.append(norm(env[i].decode("utf-8", "replace")))
            i += 1
        return sorted(got)
    except Exception as e:
        return [f"<unread: {type(e).__name__}>"]


def path_view():
    """sys.path's entries, and for each name more than one of them gives,
    the first that gives it."""
    entries = list(dict.fromkeys(os.path.realpath(p or ".") for p in sys.path))
    first, count = {}, collections.Counter()
    for q in entries:
        try:
            got = os.listdir(q)
        except OSError:
            continue
        for f in got:
            n, ext = os.path.splitext(f)
            n = n.split(".")[0]
            if not n.isidentifier() or n.startswith("__") or not (
                    ext in (".py", ".pyc", ".so", ".pyd") or (not ext and os.path.isdir(
                        os.path.join(q, f)))):
                continue
            count[n] += 1
            first.setdefault(n, norm(q))
    return {"entries": sorted(norm(q) for q in entries),
            "shadowed": {n: first[n] for n in sorted(first) if count[n] > 1}}


def decimal_view():
    c = decimal.getcontext()
    return [c.prec, str(c.rounding), c.Emin, c.Emax, c.capitals, c.clamp,
            sorted(k.__name__ for k, on in c.traps.items() if on)]


def named(x):
    """X by module and name: a class or a function itself, else its class."""
    o = x if isinstance(x, (type, types.FunctionType, types.BuiltinFunctionType)) else type(x)
    return f"{getattr(o, '__module__', '?')}.{getattr(o, '__qualname__', '?')}"


def process_view():
    """The process's state outside the repository's modules that a test
    meets (TJ-3966): the warnings filters (in order: the first that fits
    wins, TJ-3981) and the function that shows a warning, the import
    system's finders and path hooks (by module and name, in order),
    logging's disable level and the root logger's level and handlers (by
    type, level and file), the umask."""
    def pat(x):
        return getattr(x, "pattern", x)
    filters = [json.dumps([a, pat(m), named(c) if isinstance(c, type) else repr(c), pat(mo), n])
               for a, m, c, mo, n in warnings.filters]
    root = logging.root
    mask = os.umask(0o022)
    os.umask(mask)
    return {"warnings": [*filters, named(warnings.showwarning)],
            "meta_path": [named(f) for f in sys.meta_path],
            "path_hooks": [named(h) for h in sys.path_hooks],
            "logging": [root.manager.disable, root.level,
                        [[named(h), h.level, norm(str(getattr(h, "baseFilename", "") or ""))]
                         for h in root.handlers]],
            "umask": oct(mask)}


HOME_MAX = 20000
home_cut = {"cut": False}


def home_view():
    """What lies below each HOME the run knows (SUBS: the setup's and the
    one it was given), by its name there, a file with its size (TJ-3966);
    past HOME_MAX entries, cut short, and home_cut says so (the check
    notes it: TJ-3981)."""
    out = set()
    for top, label in SUBS:
        for d, dirs, files in os.walk(top):
            dirs.sort()
            rel = os.path.relpath(d, top)
            out.update(f"{label}/{os.path.normpath(os.path.join(rel, x))}/" for x in dirs)
            for f in files:
                try:
                    size = os.lstat(os.path.join(d, f)).st_size
                except OSError:
                    size = -1
                out.add(f"{label}/{os.path.normpath(os.path.join(rel, f))} {size}")
            if len(out) > HOME_MAX:
                home_cut["cut"] = True
                return sorted(out)[:HOME_MAX] + ["<cut>"]
    return sorted(out)


def repository_module(mm):
    return isinstance(mm, types.ModuleType) and not fake(mm) \
        and mine(getattr(mm, "__file__", None))


def module_row(name, mm):
    """Module NAME's names but its dunders, its imports of modules (by
    name) and the memos MEMOS names (TJ-3870) but cache wrappers (CACHED,
    walked without their entries: TJ-3975): a digest each, and the names
    cut short."""
    row, cut = {}, []
    for k, x in sorted(vars(mm).items()):
        if (k.startswith("__") and k.endswith("__")) \
                or (isinstance(x, types.ModuleType) and not fake(x)) \
                or (f"{name}.{k}" in MEMOS and f"{name}.{k}" not in CACHED):
            continue
        row[k] = hashlib.sha1(snap(x).encode()).hexdigest()[:16]
        if walk["cut"]:
            cut.append(k)
    return row, cut


def digests(skip):
    """Each repository module's row (`module_row`), but SKIP's."""
    return {name: list(module_row(name, mm)) for name, mm in list(sys.modules.items())
            if name not in skip and repository_module(mm)}


def is_fake_entry(k, v):
    """sys.modules' K holds something no import of the repository's made:
    a module without a spec (or a bare one), wherever it is planted; a
    module whose spec names another than K (TJ-3867), but one outside the
    repository below an outside package (os.path); or no module at all,
    but a value an outside package keeps below its own name (typing.io, a
    cffi lib) (TJ-3850). Not __main__ (nor multiprocessing's alias)."""
    if k in ("__main__", "__mp_main__"):
        return False
    top = sys.modules.get(k.split(".")[0])
    below_outside = k != k.split(".")[0] and isinstance(top, types.ModuleType) \
        and not fake(top) and not mine(getattr(top, "__file__", None))
    if issubclass(type(v), types.ModuleType):    # (a cffi lib only says it is one)
        if fake(v):
            return True
        if getattr(v.__spec__, "name", k) != k:  # planted under another name
            return not (below_outside and not mine(getattr(v, "__file__", None)))
        return False
    return not below_outside


def world(skip):
    return {"environ": c_environ(), "path": path_view(), "decimal": decimal_view(),
            "random": hashlib.sha1(repr(random.getstate()).encode()).hexdigest(),
            "process": process_view(), "home": home_view(),
            "fakes": {k: snap(v) for k, v in list(sys.modules.items()) if is_fake_entry(k, v)},
            "mods": digests(skip)}


def changed(a, b):
    """What B, a world after A, changed of A's: environment variables,
    sys.path, the decimal context, the random state, the process's own
    state (`process_view`), what lies below HOME, fake modules, names of
    the modules A had (MEMOS left out by `digests`)."""
    out = sorted({x.split("=")[0] for x in set(a["environ"]) ^ set(b["environ"])})
    out += [k for k in ("path", "decimal", "random") if a[k] != b[k]]
    out += [k for k in sorted(a["process"]) if a["process"][k] != b["process"].get(k)]
    out += sorted({x.rsplit(" ", 1)[0] for x in set(a["home"]) ^ set(b["home"])})
    out += [f"sys.modules[{k!r}]" for k in sorted(set(a["fakes"]) | set(b["fakes"]))
            if a["fakes"].get(k) != b["fakes"].get(k)]
    for m, (row, _cut) in a["mods"].items():
        new = b["mods"].get(m, [{}, []])[0]
        out += [f"{m}.{k}" for k in sorted(set(row) | set(new)) if row.get(k) != new.get(k)]
    return out


def own_imports(name, origin):
    """The modules NAME's module-level code imports (not a def's or a
    class's), resolved."""
    try:
        tree = ast.parse(open(origin, "rb").read())
    except Exception:
        return []
    pkg = name if origin.endswith("__init__.py") else name.rpartition(".")[0]
    out, todo = [], list(tree.body)
    while todo:
        n = todo.pop(0)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            try:
                base = importlib.util.resolve_name("." * n.level + (n.module or ""), pkg) \
                    if n.level else n.module
            except Exception:
                continue
            out += [base] + [f"{base}.{a.name}" for a in n.names if a.name != "*"]
        todo += [x for x in ast.iter_child_nodes(n) if isinstance(x, ast.stmt)] + [
            s for h in getattr(n, "handlers", ()) for s in h.body]
    return out


def theirs(top):
    """The top-level module TOP is not the repository's (the standard
    library's, a package's, a builtin): found without importing anything
    (a loaded module's is its `__spec__`; one without is not counted)."""
    try:
        spec = importlib.util.find_spec(top)
    except BaseException:
        return False
    if spec is None:
        return False
    locs = [spec.origin] if spec.has_location else list(spec.submodule_search_locations or ())
    return not any(mine(x) for x in locs)


def lost_effects(others, skip):
    """Of OTHERS (the modules a run of BASE loaded, in the order their
    imports finished: importlib moves a module to the end of sys.modules
    once its code has run, so one another imports comes first; each with
    its file under the root, None when it is no module of the
    repository's) each of the repository's this run has not: what
    importing it changes (TJ-3829). The world is taken first, then the
    modules outside the repository it imports are imported (what they
    do is theirs), then it, with its parent packages and the modules of
    the repository it imports (TJ-3841). SKIP's names, exact, are left
    out (TJ-3936): the sources' and the setup's own imports are what the
    world compares."""
    found = {}
    for name, rel in others:
        if rel is None or name in sys.modules or name in skip:
            continue
        for dep in own_imports(name, os.path.join(real, rel)):
            if dep not in sys.modules and theirs(dep.split(".")[0]):
                try:
                    importlib.import_module(dep)
                except BaseException:
                    pass
        if name in sys.modules:
            continue
        before = world(skip)
        try:
            importlib.import_module(name)
        except BaseException as e:
            found[name] = [f"its import fails ({type(e).__name__})"]
            continue
        what = changed(before, world(skip))
        if what:
            found[name] = what
    return found


# A lost module whose state moves with the environment: after the move the
# code that first imports it may be a test's, under an environment of its
# own (TJ-3869, TJ-3933); one whose replay fails, the check cannot tell.
MOVES = "its state moves with the environment: "
AGAIN = "its replay fails, so the check cannot tell"
# The hermetic setup's record of its HOME: a test patches HOME, not this.
SETUP_HOME = "TAXJSON_TEST_HOME"
# What a moved fork never unsets nor gives a test's value: HOME (moved), the
# setup's record of it, and TAXJSON_OFFLINE, which stays on in another
# spelling: the check runs offline (TJ-3967).
KEPT = ("HOME", SETUP_HOME, "TAXJSON_OFFLINE")
# What a moved fork's import touches below the moved HOME (TJ-3904, TJ-3934,
# TJ-3964), a file that is not there too: each path argument (by its place)
# of the audit events below: open; the os calls that list, scan, walk,
# make, remove, rename, link, change or enter a path; shutil's, tempfile's,
# glob's and pathlib's globs; sqlite3.connect, ctypes.dlopen. The calls that
# raise no event (os.stat, os.lstat, os.access, os.readlink, os.statvfs,
# os.mkfifo, os.mknod: os.path.exists, isfile, lexists, realpath, a Path's
# stat) are wrapped while it imports.
PATH_ARGS = {
    "open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.walk": (0,), "os.fwalk": (0,),
    "os.mkdir": (0,), "os.rmdir": (0,), "os.remove": (0,), "os.rename": (0, 1),
    "os.link": (0, 1), "os.symlink": (0, 1), "os.chmod": (0,), "os.chown": (0,),
    "os.chflags": (0,), "os.lchflags": (0,), "os.utime": (0,), "os.truncate": (0,),
    "os.chdir": (0,), "os.listxattr": (0,), "os.getxattr": (0,), "os.setxattr": (0,),
    "os.removexattr": (0,), "shutil.copyfile": (0, 1), "shutil.copymode": (0, 1),
    "shutil.copystat": (0, 1), "shutil.copytree": (0, 1), "shutil.move": (0, 1),
    "shutil.rmtree": (0,), "shutil.chown": (0,), "shutil.make_archive": (0, 2, 3),
    "shutil.unpack_archive": (0, 1), "tempfile.mkdtemp": (0,), "tempfile.mkstemp": (0,),
    "glob.glob": (0,), "glob.glob/2": (0, 2), "pathlib.Path.glob": (0,),
    "pathlib.Path.rglob": (0,), "sqlite3.connect": (0,), "ctypes.dlopen": (0,)}
touched = {"below": None, "paths": set()}


def _touch(p):
    try:
        p = os.path.abspath(os.fsdecode(p))
    except Exception:           # a file descriptor, None
        return
    for h in touched["below"]:
        if p == h or p.startswith(h + os.sep):
            touched["paths"].add("~" + p[len(h):])


def _audit(event, args):
    if touched["below"] is not None:
        for i in PATH_ARGS.get(event, ()):
            if i < len(args):
                _touch(args[i])


def _watched(f):
    def watched(path, *a, **k):
        if touched["below"] is not None:
            _touch(path)
        return f(path, *a, **k)
    return watched


WATCH = tuple(n for n in ("stat", "lstat", "access", "readlink", "statvfs", "mkfifo", "mknod")
              if hasattr(os, n))
REAL = {n: getattr(os, n) for n in WATCH}
WATCHED = {n: _watched(f) for n, f in REAL.items()}
ALIASES.update({id(WATCHED[n]): REAL[n] for n in WATCH})  # (`from os import stat` keeps it)
# The variables a fork's import reads (TJ-3967): os.environ's (and environb's)
# reads by name, "*" when it is walked or copied (`dict(os.environ)`,
# `.items()`, `.copy()`). C's getenv (time.tzset's TZ) is not seen.
reads = set()
ENVIRON = type(os.environ)
REAL_ENV = (ENVIRON.__getitem__, ENVIRON.__iter__)


def _getitem(self, key):
    reads.add(os.fsdecode(key) if isinstance(key, bytes) else key)
    return REAL_ENV[0](self, key)


def _iter(self):
    reads.add("*")
    return REAL_ENV[1](self)


def under_home(k):
    """Environment variable K is HOME or lies below it (XDG_*_HOME, the
    hermetic mark)."""
    h, v = os.environ.get("HOME", ""), os.environ.get(k, "")
    return k == "HOME" or (len(h) > 3 and (v == h or v.startswith(h + os.sep)))


def own_value(k, v, home):
    """A value of its own for variable K, now V, in the moved environment
    whose HOME is HOME (TJ-3937): a path below HOME for a path, the next
    number for a number, "1" for one not set, else V with a suffix; but
    TAXJSON_OFFLINE stays on (another spelling): the check runs offline."""
    if k == "TAXJSON_OFFLINE":
        return "true" if v != "true" else "yes"
    if v is None:
        return "1"
    if os.path.isabs(v):
        return os.path.join(home, "env", k)
    if re.fullmatch(r"-?[0-9]+", v):
        return str(int(v) + 1)
    return v + "-moved"


def moved_environment(home, names):
    """The environment a test may have made before it first imports a
    module: HOME moved to HOME, each variable below it with it, and each
    of NAMES (the variables the tree's tests set) given a value of its
    own; the setup's record of its HOME (SETUP_HOME) as it is."""
    h = os.environ.get("HOME", "")
    env = {k: home + v[len(h):] for k, v in os.environ.items()
           if k not in ("HOME", SETUP_HOME) and under_home(k)}
    env["HOME"] = home
    for k in names:
        if k not in env and k != SETUP_HOME:
            env[k] = own_value(k, os.environ.get(k), home)
    return env


def replayed(name, home, env):
    """Import NAME (in a fork: this process as HEAD's import left it),
    with ENV (a value each, None to unset) and its touches below HOME
    recorded when HOME is given: the row of each module of the repository
    the import loaded (`module_row`; another module a name holds, by its
    name), the paths below HOME it touched, the variables it read, and
    the process's views after it (`process_view`, sys.path, the decimal
    context, what it changed of the environment: TJ-3976; the fake
    modules, `is_fake_entry`: TJ-3991)."""
    before = set(sys.modules)
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    env0 = set(c_environ())
    if home:
        touched["below"] = sorted({home, os.path.realpath(home)})
        for n in WATCH:
            setattr(os, n, WATCHED[n])
    ENVIRON.__getitem__, ENVIRON.__iter__ = _getitem, _iter
    try:
        importlib.import_module(name)
    finally:
        ENVIRON.__getitem__, ENVIRON.__iter__ = REAL_ENV
        touched["below"] = None
        for n in WATCH:
            setattr(os, n, REAL[n])
    rows = {}
    for k, mm in list(sys.modules.items()):
        if k not in before and repository_module(mm):
            row, cut = module_row(k, mm)
            row.update({x: f"<module {v.__name__}>" for x, v in vars(mm).items()
                        if isinstance(v, types.ModuleType) and not fake(v)
                        and not (x.startswith("__") and x.endswith("__"))})
            row.update({x: "<cut>" for x in cut})
            rows[k] = row
    env1 = set(c_environ())
    views = {"process": process_view(), "path": path_view(), "decimal": decimal_view(),
             "environ": sorted(env1 - env0) + sorted("-" + x for x in env0 - env1),
             "fakes": {k: hashlib.sha1(snap(v).encode()).hexdigest()[:16]
                       for k, v in list(sys.modules.items()) if is_fake_entry(k, v)}}
    return {"rows": rows, "touched": sorted(touched["paths"]), "reads": sorted(reads),
            "views": views}


FORK_TIMEOUT = 300


def _kill(pid):
    """PID's process group, PID if it has none yet (TJ-3970)."""
    for kill in (os.killpg, os.kill):
        try:
            kill(pid, signal.SIGKILL)
            return
        except OSError:
            pass


def forked(fn, *args):
    """FN(*ARGS) in a fork of this process, its result (JSON); None when
    it fails, the fork does, or it takes over FORK_TIMEOUT s. The fork
    starts a session of its own, so a timeout kills what its import
    started too, and so does its end (TJ-3970)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:                # the fork: nothing of it flows back but its result
        os.close(r)
        try:
            os.setsid()
        except OSError:
            pass
        try:
            data = json.dumps(fn(*args)).encode()
        except BaseException:
            data = b""
        try:
            with os.fdopen(w, "wb") as f:
                f.write(data)
        finally:
            os._exit(0)
    os.close(w)
    chunks, end = [], time.monotonic() + FORK_TIMEOUT
    with os.fdopen(r, "rb", buffering=0) as f:
        while True:
            if not select.select([f], [], [], max(0.0, end - time.monotonic()))[0]:
                _kill(pid)
                os.waitpid(pid, 0)
                return None
            chunk = f.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
    data = b"".join(chunks)
    if hasattr(os, "waitid"):   # exited, not reaped: its group's id is still its own
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        _kill(pid)              # what it started and left running
    _pid, status = os.waitpid(pid, 0)
    try:
        return json.loads(data) if data and status == 0 else None
    except ValueError:
        return None


def _differ(a, b, cut=False):
    """Two replays' rows: for each module, the names that differ (with
    CUT, that either cut short); a module only one loaded, by "*"."""
    out = {}
    for k in set(a) | set(b):
        x, y = a.get(k), b.get(k)
        if x is None or y is None:
            ns = [] if cut else ["*"]
        else:
            ns = [n for n in set(x) | set(y) if ("<cut>" in (x.get(n), y.get(n))) == cut
                  and (cut or x.get(n) != y.get(n))]
        if ns:
            out[k] = set(ns)
    return out


def _views_differ(a, b):
    """Where two replays' process views differ (`replayed`, TJ-3976): a
    key of `process_view`, sys.path, the decimal context, each variable
    the imports changed differently, each fake module (TJ-3991)."""
    va, vb = a.get("views") or {}, b.get("views") or {}
    pa, pb = va.get("process") or {}, vb.get("process") or {}
    out = {k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k)}
    out |= {label for k, label in (("path", "sys.path"), ("decimal", "the decimal context"))
            if va.get(k) != vb.get(k)}
    out |= {"environ " + x.lstrip("-").split("=")[0]
            for x in set(va.get("environ") or []) ^ set(vb.get("environ") or [])}
    fa, fb = va.get("fakes") or {}, vb.get("fakes") or {}
    out |= {f"sys.modules[{k!r}]" for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)}
    return out


def homes_state():
    """Each HOME the run knows (SUBS): its folders, its files with their
    size and time."""
    out = []
    for top, _label in SUBS:
        for d, dirs, files in os.walk(top):
            out += [os.path.join(d, x) + "/" for x in dirs]
            for f in files:
                p = os.path.join(d, f)
                try:
                    s = os.lstat(p)
                    out.append(f"{p} {s.st_size} {s.st_mtime_ns}")
                except OSError:
                    out.append(p)
    return sorted(out)


class Homes:
    """A copy of each HOME the run knows, put back when a fork changed one
    (the unmoved forks import under the setup's HOME): what one fork
    writes there is not what the next one, or `lost_effects`, starts from."""

    def __init__(self):
        self.copy = tempfile.mkdtemp(prefix="tj-homes-")
        self.tops = sorted({top for top, _label in SUBS if os.path.isdir(top)})
        for i, top in enumerate(self.tops):
            shutil.copytree(top, os.path.join(self.copy, str(i)), symlinks=True)
        self.state = homes_state()

    def back(self):
        if homes_state() == self.state:
            return
        for i, top in enumerate(self.tops):
            for x in os.listdir(top):
                p = os.path.join(top, x)
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.unlink(p)
            shutil.copytree(os.path.join(self.copy, str(i)), top, symlinks=True,
                            dirs_exist_ok=True)
        self.state = homes_state()

    def drop(self):
        shutil.rmtree(self.copy, ignore_errors=True)


# The forks that give a test's own values (TJ-3967), at most: the tree's
# largest count (TAXJSON_WIDTH's seven) and one more (TJ-3978); the
# constants a replay leaves out above it, by name (UNTRIED), are noted.
CONSTANT_FORKS = 8
UNTRIED = {}


def replay(lost, envs):
    """Each of LOST (the modules of the repository BASE loaded and this
    import did not) as a test meets it (TJ-3933): forks of this process,
    as HEAD's import left it, import it, one as it is and the others with
    the environment moved just before, each with a HOME of its own: one
    by `moved_environment` (ENVS' names); when the import reads a name
    the tests remove (ENVS' deletes) or walks the environment, one with
    those unset too; when it reads a name the tests give constants (ENVS'
    values), one per constant (CONSTANT_FORKS at most, the rest in
    UNTRIED), each such name at its next one (TJ-3967). The rows of every
    module of the repository each import loaded, and the process's views
    after it, are compared with the unmoved fork's (`_differ`,
    `_views_differ`): but what differs as well between two forks that
    move nothing (the clock, randomness: the suite's). Each module whose names differ or
    are cut short moves (it is one of LOST too); the one imported moves
    by each module only one fork loaded and each path below a moved HOME
    its import touched. [AGAIN] for the module imported when a fork
    fails, or there is no os.fork. The HOMEs the run knows are put back
    after each fork (`Homes`)."""
    if not lost:
        return {}
    if not hasattr(os, "fork"):
        return {k: [AGAIN] for k in lost}
    out = {}
    names = envs.get("names", [])
    gone = [k for k in envs.get("deletes", []) if k not in KEPT]
    values = {k: [v for v in vs if v != os.environ.get(k)]
              for k, vs in envs.get("values", {}).items() if k not in KEPT}
    values = {k: vs for k, vs in values.items() if vs}

    def say(k, xs):
        out.setdefault(k, set()).update(xs)

    def fork(name, moved=False, unset=(), consts=None):
        """A fork's replay of NAME: unmoved, or MOVED (a HOME of its own,
        removed after) with UNSET unset and CONSTS set."""
        home, env = "", {}
        if moved:
            home = tempfile.mkdtemp(prefix="tj-moved-")
            env = moved_environment(home, names)
            env.update({k: None for k in unset})
            env.update(consts or {})
        try:
            return forked(replayed, name, home, env)
        finally:
            homes.back()
            if home:
                shutil.rmtree(home, ignore_errors=True)
    homes = Homes()
    try:
        for name in lost:
            a, b = fork(name), fork(name, moved=True)
            forks = [b]
            if a is not None and b is not None:
                read = set(a["reads"]) | set(b["reads"])
                every = "*" in read
                if gone and (every or read & set(gone)):
                    forks.append(fork(name, moved=True, unset=gone))
                keys = sorted(k for k in values if every or k in read)
                UNTRIED.update({k: values[k][CONSTANT_FORKS:] for k in keys
                                if len(values[k]) > CONSTANT_FORKS})
                for i in range(min(CONSTANT_FORKS, max([len(values[k]) for k in keys] or [0]))):
                    forks.append(fork(name, moved=True, consts={
                        k: values[k][min(i, len(values[k]) - 1)] for k in keys}))
            if a is None or None in forks:
                say(name, [AGAIN])
                continue
            moved, cuts, views = {}, {}, set()
            for f in forks:
                for k, ns in _differ(a["rows"], f["rows"]).items():
                    moved.setdefault(k, set()).update(ns)
                for k, ns in _differ(a["rows"], f["rows"], cut=True).items():
                    cuts.setdefault(k, set()).update(ns)
                views |= _views_differ(a, f)
            if moved or views:
                again = fork(name)
                if again is None:
                    say(name, [AGAIN])
                    continue
                noise = _differ(a["rows"], again["rows"])
                moved = {k: ns - noise.get(k, set()) for k, ns in moved.items()}
                views -= _views_differ(a, again)
            if views:                   # what it changes of the process (TJ-3976)
                say(name, views)
            for k, ns in [*moved.items(), *cuts.items()]:
                if "*" in ns:
                    say(name, [k])
                if ns - {"*"}:
                    say(k, ns - {"*"})
            for f in forks:
                if f["touched"]:
                    say(name, f["touched"])
    finally:
        homes.drop()
    return {k: [AGAIN] if AGAIN in xs else sorted(xs) for k, xs in out.items()}


def made(before):
    """The HOMEs by name: the one the hermetic setup made, if HOME moved
    since BEFORE, and the one this run was given (the setup's other
    variables lie below its HOME). Only those: a value another import
    derived from either keeps which one it was (TJ-3868); but when MERGE
    (a run-alone view where only HEAD's import runs the setup: TJ-3969),
    both are the given one's."""
    home = os.environ.get("HOME", "")
    mine_ = "<HOME0>" if merge else "<HOME>"
    return sorted(([(home, mine_)] if home != before.get("HOME") and len(home) > 3 else [])
                  + ([(pre["HOME"], "<HOME0>")] if len(pre.get("HOME", "")) > 3 else []),
                  key=lambda kv: -len(kv[0]))
'''
_STATE_MAIN = r'''
sys.addaudithook(_audit)
root, mod, names, skip, others, out, preload, envs = (
    sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), set(json.loads(sys.argv[4])),
    json.loads(sys.argv[5]), sys.argv[6], sys.argv[7], json.loads(sys.argv[8]))
# PRELOAD: "1" the setup first, as the runner and discovery import it; "0"
# none (run alone); "0m" none, both HOMEs one name (`made`); "endm" none,
# the setup after the import, both HOMEs one name (TJ-3969).
first, merge, after = preload == "1", preload.endswith("m"), preload.startswith("end")
real = os.path.realpath(root)
sys.path.insert(0, os.path.realpath("."))
random.seed(0)
pre = dict(os.environ)
spec = importlib.util.find_spec("_hermetic")
setup = spec is not None and mine(spec.origin)
if first and setup:
    importlib.import_module("_hermetic")          # first, as the runner and discovery do
SUBS = made(pre)
env0, home0 = set(c_environ()), set(home_view())
try:
    m = importlib.import_module(mod)
except BaseException as e:
    json.dump({"error": f"{type(e).__name__}: {e}"[:300]}, open(out, "w"))
    sys.exit(0)
if after and setup:
    importlib.import_module("_hermetic")          # BASE's import run alone, then the setup
if not first:                   # run alone: what its own imports' hermetic setup made
    SUBS = made(pre)
hermetic = "_hermetic" in sys.modules   # its import's own (not lost_effects')
skip.add(mod)
# The lost modules the replay leaves out, by exact name (TJ-3936): the
# support package and this batch's support modules; a source and the setup
# are replayed too.
unseen = {n for n in skip if n.split(".")[0] == "_support"} | {mod}
# Each lost module replayed first: forks of the process as this import left it.
moves = replay([n for n, rel in others if rel is not None and n not in sys.modules
                and n not in unseen], envs)
if names == ["*"]:
    names = [k for k in vars(m) if not (k.startswith("__") and k.endswith("__"))]
values, cut = {}, []
for n in names:
    if hasattr(m, n):
        values[n] = snap(getattr(m, n))
        if walk["cut"]:
            cut.append(n)
lost = lost_effects(others, skip)
for name, _rel in others:       # BASE's others (the standard library's, a package's) too
    if name not in sys.modules and name not in skip:
        try:
            importlib.import_module(name)
        except BaseException:
            pass
w = world(skip)                 # (on equal terms: what BASE loaded, loaded)
env1, home1 = set(w["environ"]), set(w["home"])     # what the imports changed
w["environ"] = sorted(env1 - env0) + sorted("-" + x for x in env0 - env1)
w["home"] = sorted(home1 - home0) + sorted("-" + x for x in home0 - home1)
files = {k: os.path.relpath(os.path.realpath(f), real) for k, f in
         ((k, getattr(v, "__file__", None)) for k, v in list(sys.modules.items())) if mine(f)}
loaded = list(sys.modules)
for name, how in moves.items():
    lost.setdefault(name, []).append(MOVES + ", ".join(how))
json.dump({"names": values, "cut": cut, "world": w, "lost": lost, "loaded": loaded,
           "files": files, "hermetic": hermetic, "untried": UNTRIED,
           "home_cut": home_cut["cut"]}, open(out, "w"))
'''
_STATE = _SNAP + _STATE_DEFS + _STATE_MAIN


_IDS = r"""
import importlib, importlib.util, json, os, sys, unittest
sys.path.insert(0, ".")
spec = importlib.util.find_spec("_hermetic")       # a package or a file, of this tree
if spec is not None and os.path.realpath(spec.origin or "/").startswith(os.path.realpath(".") + os.sep):
    importlib.import_module("_hermetic")
loader = unittest.TestLoader()
ids, marks = [], []
# What runs a test besides its code (TJ-3946): its tax-rules markers
# (tests/tax_rules records them as `__tax_rules__`) and unittest's skip.
def mark(o):
    return [sorted(map(repr, list(getattr(o, "__tax_rules__", None) or []))),
            bool(getattr(o, "__unittest_skip__", False)), str(getattr(o, "__unittest_skip_why__", ""))]
def walk(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            walk(t)
        else:
            ids.append(t.id())
            fn = getattr(type(t), getattr(t, "_testMethodName", ""), None)
            marks.append([t.id(), mark(fn), mark(type(t))])
walk(loader.discover(".", "test_*.py", top_level_dir="."))
json.dump({"ids": ids, "marks": marks, "errors": [str(e)[:300] for e in loader.errors]},
          open(sys.argv[1], "w"))
"""


def moved_names(support: Path) -> Set[str]:
    """The names a support module defines, not imports."""
    tree = ast.parse(MT.read_exact(support))
    return {b for s in tree.body if not isinstance(s, (ast.Import, ast.ImportFrom, ast.Expr))
            for b in module_binds(s)}


def _child_env(tree: Path, home: Path, extra: str = "") -> Dict[str, str]:
    """A child's environment: none of ours that steers Python, git or
    taxjson; TREE's src and each of its packages' on the path (TJ-3837)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TAXJSON_", "PYTHON", "GIT_"))}
    srcs = [str(tree / "src")] + [str(p) for p in sorted((tree / "packages").glob("*/src"))]
    env.update(PYTHONPATH=os.pathsep.join(filter(None, (extra, *srcs))),
               HOME=str(home), TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    return env


TIMEOUT = 1800


def _is_environ(x: ast.AST) -> bool:
    return (isinstance(x, ast.Attribute) and x.attr == "environ") \
        or (isinstance(x, ast.Name) and x.id == "environ") \
        or (isinstance(x, ast.Constant) and x.value == "os.environ")


def _str(x: Optional[ast.AST]) -> Optional[str]:
    return x.value if isinstance(x, ast.Constant) and isinstance(x.value, str) else None


def _environ_comp(x: ast.AST) -> Optional[Set[str]]:
    """X is a dict comprehension over os.environ (its items, its keys or
    itself): the names its conditions leave out (`k != K`, `k not in
    (...)`; `not k.startswith(P)` as "P*"); else None."""
    if not isinstance(x, ast.DictComp) or len(x.generators) != 1:
        return None
    g = x.generators[0]
    it = g.iter.func.value if isinstance(g.iter, ast.Call) and isinstance(
        g.iter.func, ast.Attribute) and g.iter.func.attr in ("items", "keys") else g.iter
    if not _is_environ(it):
        return None
    key = g.target.elts[0] if isinstance(g.target, ast.Tuple) and g.target.elts else g.target
    k = key.id if isinstance(key, ast.Name) else None
    out: Set[str] = set()
    todo = list(g.ifs)
    while todo:
        c = todo.pop()
        if isinstance(c, ast.BoolOp) and isinstance(c.op, ast.And):
            todo += c.values
        elif isinstance(c, ast.Compare) and len(c.ops) == 1 and isinstance(c.left, ast.Name) \
                and c.left.id == k:
            r = c.comparators[0]
            if isinstance(c.ops[0], ast.NotEq):
                out.update(filter(None, [_str(r)]))
            elif isinstance(c.ops[0], ast.NotIn) and isinstance(r, (ast.Tuple, ast.List, ast.Set)):
                out.update(filter(None, map(_str, r.elts)))
        elif isinstance(c, ast.UnaryOp) and isinstance(c.op, ast.Not) \
                and isinstance(c.operand, ast.Call) and isinstance(c.operand.func, ast.Attribute) \
                and c.operand.func.attr == "startswith" and c.operand.args \
                and isinstance(c.operand.func.value, ast.Name) and c.operand.func.value.id == k:
            p = c.operand.args[0]
            out.update(x + "*" for x in filter(None, map(_str, getattr(p, "elts", [p]))))
    return out


# A mapping a test gives patch.dict or os.environ.update, as `_mapping`
# reads it: its constant keys, each with its value (None when not a
# constant); whether it keeps the rest of the environment (True), holds
# only those (False) or cannot be told (None); the names it leaves out.
Mapping = Tuple[List[Tuple[str, Optional[str]]], Optional[bool], Set[str]]


def _mapping(x: Optional[ast.AST], scope: Dict[str, list], depth: int = 0) -> Mapping:
    """X (a dict display, `dict(...)`, a comprehension over os.environ, a
    copy of it, or a name SCOPE binds to one of those, with SCOPE's
    constant stores and removals on it) as a Mapping."""
    if x is None:
        return [], False, set()
    if isinstance(x, ast.Name):
        bound = scope.get(x.id)
        if not bound or depth > 4:
            return [], None, set()
        pairs: List[Tuple[str, Optional[str]]] = []
        rests, drops = [], set()
        for v in bound:
            if isinstance(v, tuple):            # a store or a removal on the name
                if v[0] == "store":
                    pairs.append(v[1])
                else:
                    drops.add(v[1])
                continue
            p, r, d = _mapping(v, scope, depth + 1)
            pairs += p
            rests.append(r)
            drops |= d
        rest = None if not rests or None in rests else False if False in rests else True
        return pairs, rest, drops
    if _is_environ(x) or (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                          and x.func.attr == "copy" and _is_environ(x.func.value)):
        return [], True, set()
    comp = _environ_comp(x)
    if comp is not None:
        return [], True, comp
    splats: List[ast.AST] = []
    if isinstance(x, ast.Dict):
        pairs = [(k, _str(v)) for k, v in ((_str(k), v) for k, v in zip(x.keys, x.values))
                 if k is not None]
        splats = [v for k, v in zip(x.keys, x.values) if k is None]
    elif isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "dict":
        pairs = [(k.arg, _str(k.value)) for k in x.keywords if k.arg]
        splats = [*x.args[:1], *(k.value for k in x.keywords if not k.arg)]
    else:
        return [], None, set()
    rest, drops = False, set()
    for v in splats:
        p, r, d = _mapping(v, scope, depth + 1)
        pairs = p + pairs
        rest = None if None in (rest, r) else rest or r
        drops |= d
    return pairs, rest, drops


def _scope(node: ast.AST) -> Dict[str, list]:
    """What NODE's code binds to each plain name: the values assigned, and
    ("store", (K, V)) / ("drop", K) for each constant store, `update`
    pair, `pop` or `del` on it."""
    out: Dict[str, list] = {}
    for n in ast.walk(node):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, []).append(n.value)
                elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                        and _str(t.slice) is not None:
                    out.setdefault(t.value.id, []).append(("store", (_str(t.slice), _str(n.value))))
        elif isinstance(n, ast.Delete):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                        and _str(t.slice) is not None:
                    out.setdefault(t.value.id, []).append(("drop", _str(t.slice)))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name):
            v = n.func.value.id
            if n.func.attr == "pop" and n.args and _str(n.args[0]) is not None:
                out.setdefault(v, []).append(("drop", _str(n.args[0])))
            elif n.func.attr in ("update", "setdefault"):
                if n.func.attr == "setdefault" and n.args and _str(n.args[0]) is not None:
                    got = [(_str(n.args[0]), _str(n.args[1]) if len(n.args) > 1 else None)]
                else:
                    got = [(k.arg, _str(k.value)) for k in n.keywords if k.arg]
                    if n.args and isinstance(n.args[0], ast.Dict):
                        got += [(k, _str(x)) for k, x in zip(map(_str, n.args[0].keys),
                                                               n.args[0].values) if k]
                out.setdefault(v, []).extend(("store", p) for p in got)
    return out


TAXJSON_NAME = re.compile(r"TAXJSON_[A-Z0-9_]+")


def set_names(tree: ast.AST) -> Set[str]:
    """The `TAXJSON_*` names a test file may set or remove (TJ-3979): each
    call keyword and each such str or bytes constant but one read where
    it stands (TJ-3989, TJ-4007): the first argument of `get` or
    `getenv`, a subscript read, the left operand of `in` or `not in`. A
    constant bound to a name counts, whatever the file does with the name
    (another module may import it and set it), and so do the items on
    the right of `in` (a filter's): over-counted rather than missed."""
    up = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

    def read(x: ast.Constant) -> bool:
        p = up.get(x)
        if isinstance(p, ast.Subscript):
            return p.slice is x and isinstance(p.ctx, ast.Load)
        if isinstance(p, ast.Call):
            f = p.func
            return bool(p.args) and p.args[0] is x \
                and (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in ("get", "getenv")
        return isinstance(p, ast.Compare) and p.left is x and isinstance(p.ops[0], (ast.In, ast.NotIn))

    def name(x: ast.Constant) -> Optional[str]:
        v = x.value.decode("latin-1") if isinstance(x.value, bytes) else x.value
        return v if isinstance(v, str) and TAXJSON_NAME.fullmatch(v) else None
    out: Set[str] = {k.arg for n in ast.walk(tree) if isinstance(n, ast.Call)
                     for k in n.keywords if k.arg and TAXJSON_NAME.fullmatch(k.arg)}
    return out | {s for c in ast.walk(tree) if isinstance(c, ast.Constant)
                  for s in [name(c)] if s and not read(c)}


def test_env(tree: Path) -> Dict[str, object]:
    """What TREE's tests do to the environment (TJ-3937, TJ-3967), read
    from every file of tests/ and packages/*/tests, each function's code
    (and the module's) on its own: "names", the variables they set or
    remove: the keys of `patch.dict(os.environ, M, K=...)` (M a dict
    display, `dict(...)`, or a name the same code binds to one, with its
    constant stores; the module's code takes in its functions', so a
    dict a module-level name holds is read in a function too: TJ-3979);
    an `os.environ[K]` store or `del`; its `setdefault`, `pop`,
    `update`; `setenv`, `putenv`, `delenv`, `unsetenv` given a
    constant; each `TAXJSON_*` name in src/ and packages/*/src; and each
    one a test file names, but where it reads it where it stands
    (`set_names`: a dict a helper builds and returns, TJ-3979, TJ-3989,
    TJ-4007).
    "values": each name's constant values there.
    "deletes": the names they remove: `del`, `pop`, `delenv`,
    `unsetenv`; with `patch.dict(..., clear=True)` every name M does not
    keep (a comprehension over os.environ keeps all but those its
    conditions leave out; an M it cannot read, none: over-counted); with
    `os.environ.clear()`, every name."""
    names: Set[str] = set()
    values: Dict[str, Set[str]] = {}
    deletes: Set[str] = set()
    kept: List[Optional[Set[str]]] = []         # each clear's names kept (None: none)

    def put(pairs: List[Tuple[str, Optional[str]]]) -> None:
        for k, v in pairs:
            names.add(k)
            if v is not None:
                values.setdefault(k, set()).add(v)
    for f in test_files(tree):
        try:
            text = f.read_text(encoding="utf-8")
            mod = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        names.update(set_names(mod))                # (TJ-3979, TJ-3989, TJ-4007)
        for node in [mod, *(n for n in ast.walk(mod)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
            scope = _scope(node)
            for x in ast.walk(node):
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute):
                    a, args = x.func.attr, x.args
                    kw = {k.arg: k.value for k in x.keywords if k.arg}
                    k0 = _str(args[0]) if args else None
                    if a == "dict" and args and _is_environ(args[0]):
                        m = args[1] if len(args) > 1 else kw.get("values")
                        pairs, rest, drops = _mapping(m, scope)
                        put(pairs + [(k, _str(v)) for k, v in kw.items()
                                     if k not in ("clear", "values")])
                        clear = kw.get("clear", args[2] if len(args) > 2 else None)
                        if isinstance(clear, ast.Constant) and clear.value:
                            if rest is None:            # cannot tell: every name
                                kept.append(None)
                            elif rest is False:
                                kept.append({k for k, _v in pairs})
                            else:
                                deletes.update(drops)
                                names.update(d for d in drops if not d.endswith("*"))
                    elif a in ("setdefault", "update") and _is_environ(x.func.value):
                        if a == "setdefault" and k0 is not None:
                            put([(k0, _str(args[1]) if len(args) > 1 else None)])
                        else:
                            put(_mapping(args[0] if args else None, scope)[0]
                                + [(k, _str(v)) for k, v in kw.items()])
                    elif a == "pop" and _is_environ(x.func.value) and k0 is not None:
                        names.add(k0)
                        deletes.add(k0)
                    elif a == "clear" and _is_environ(x.func.value):
                        kept.append(None)
                    elif a in ("setenv", "putenv") and k0 is not None:
                        put([(k0, _str(args[1]) if len(args) > 1 else None)])
                    elif a in ("delenv", "unsetenv") and k0 is not None:
                        names.add(k0)
                        deletes.add(k0)
                if isinstance(x, (ast.Assign, ast.AugAssign, ast.Delete)):
                    targets = x.targets if not isinstance(x, ast.AugAssign) else [x.target]
                    for t in targets:
                        if isinstance(t, ast.Subscript) and _is_environ(t.value) \
                                and _str(t.slice) is not None:
                            k = _str(t.slice)
                            names.add(k)
                            if isinstance(x, ast.Delete):
                                deletes.add(k)
                            elif isinstance(x, ast.Assign) and _str(x.value) is not None:
                                values.setdefault(k, set()).add(_str(x.value))
    for p in [*sorted(tree.glob("src/**/*.py")), *sorted(tree.glob("packages/*/src/**/*.py"))]:
        names.update(re.findall(r"\bTAXJSON_[A-Z0-9_]+", p.read_text(encoding="utf-8",
                                                                     errors="replace")))
    for keep in kept:
        deletes.update(names - (keep or set()))
    for d in [d for d in deletes if d.endswith("*")]:
        deletes.discard(d)
        deletes.update(n for n in names if n.startswith(d[:-1]))
    return {"names": sorted(names), "deletes": sorted(deletes),
            "values": {k: sorted(vs) for k, vs in sorted(values.items())}}


def test_env_names(tree: Path) -> Set[str]:
    """The environment variables TREE's tests set or remove (`test_env`)."""
    return set(test_env(tree)["names"])


def state(tree: Path, home: Path, mod: str, names: List[str], skip: Set[str],
          out: Path, others: Iterable[Tuple[str, Optional[str]]] = (),
          preload: Union[bool, str] = True, envs: Optional[dict] = None) -> dict:
    """What `import MOD` leaves in TREE, and what importing each of
    OTHERS (name, file) it did not load changes, and how each moves with
    the environment (ENVS: what the tests do to it, `test_env`): _STATE's
    snapshot. PRELOAD: True, the hermetic setup first, as the runner and
    discovery import it; False, as a module run alone imports it; or
    _STATE's own mode ("0m", "endm": TJ-3969)."""
    mode = "1" if preload is True else "0" if preload is False else preload
    try:
        r = subprocess.run([sys.executable, "-B", "-c", _STATE, str(tree), mod, json.dumps(names),
                            json.dumps(sorted(skip)), json.dumps([list(o) for o in others]),
                            str(out), mode, json.dumps(envs or {})],
                           cwd=tree / "tests", env=_child_env(tree, home), capture_output=True,
                           text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"the snapshot took over {TIMEOUT} s (TIMEOUT)"}
    if r.returncode or not out.is_file():
        return {"error": f"the snapshot failed: {(r.stderr or r.stdout).strip()[-300:]}"}
    return json.loads(out.read_text(encoding="utf-8"))


def state_diff(a: dict, b: dict, names: Optional[Set[str]] = None) -> Set[Tuple[str, ...]]:
    """Where B differs from A, over NAMES (A's all when not given): a
    name's value, or one cut short on either side; an environment
    variable; sys.path; the decimal context; the random state; the
    process's own state (`process_view`); a path below HOME made, removed
    or resized; a fake module; a name of another module of the repository
    both loaded; an import that failed."""
    out: Set[Tuple[str, ...]] = {("error", side, s["error"]) for side, s in
                                 (("BASE", a), ("HEAD", b)) if "error" in s}
    if out:
        return out
    names = set(a["names"]) if names is None else names
    out |= {("name", n) for n in names if a["names"].get(n) != b["names"].get(n)}
    out |= {("deep", n) for n in (set(a["cut"]) | set(b["cut"])) & names}
    wa, wb = a["world"], b["world"]
    out |= {("environ", x.lstrip("-").split("=")[0])
            for x in set(wa["environ"]) ^ set(wb["environ"])}
    pa, pb = wa["path"], wb["path"]
    out |= {("path", f"entry {e}") for e in set(pa["entries"]) ^ set(pb["entries"])}
    out |= {("path", f"{n} from {pa['shadowed'].get(n)} or {pb['shadowed'].get(n)}")
            for n in set(pa["shadowed"]) | set(pb["shadowed"])
            if pa["shadowed"].get(n) != pb["shadowed"].get(n)}
    out |= {(k,) for k in ("decimal", "random") if wa[k] != wb[k]}
    out |= {("process", k) for k in set(wa["process"]) | set(wb["process"])
            if wa["process"].get(k) != wb["process"].get(k)}
    out |= {("home", x.lstrip("-").rsplit(" ", 1)[0]) for x in set(wa["home"]) ^ set(wb["home"])}
    out |= {("fake", k) for k in set(wa["fakes"]) | set(wb["fakes"])
            if wa["fakes"].get(k) != wb["fakes"].get(k)}
    for m in set(wa["mods"]) & set(wb["mods"]):
        (x, xc), (y, yc) = wa["mods"][m], wb["mods"][m]
        out |= {("mod", m, k) for k in set(x) | set(y) if x.get(k) != y.get(k)}
        out |= {("deep", f"{m}.{k}") for k in set(xc) | set(yc)}
    return out


def _listed(xs: List[str], n: int = 6) -> str:
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


def _say(what: str, d: Set[Tuple[str, ...]]) -> List[str]:
    """D's differences as lines, every one counted."""
    out = [f"state: {what}: {k[1]}'s import fails: {k[2]}" for k in sorted(d) if k[0] == "error"]
    out += [f"state: {what}: {k[1]} holds something else" for k in sorted(d) if k[0] == "name"]
    for kind, label in (("environ", "the environment differs"), ("path", "sys.path differs"),
                        ("process", "the process differs"),
                        ("home", "what lies below HOME differs"),
                        ("fake", "fake modules differ"),
                        ("deep", "too deep to compare (DEPTH, BUDGET in _SNAP), or holding "
                                 "what the walk sees by its type alone")):
        xs = sorted(k[1] for k in d if k[0] == kind)
        if xs:
            out.append(f"state: {what}: {label}: {_listed(xs)}")
    out += [f"state: {what}: the {k[0]} {'context' if k[0] == 'decimal' else 'state'} differs"
            for k in sorted(d) if k[0] in ("decimal", "random")]
    mods: Dict[str, List[str]] = {}
    for k in sorted(d):
        if k[0] == "mod":
            mods.setdefault(k[1], []).append(k[2])
    out += [f"state: {what}: {m}'s {_listed(ks)} differ" for m, ks in sorted(mods.items())]
    return out


# The child's words for a lost module whose state moves with the
# environment, and for one whose replay fails (one copy).
MOVES = re.search(r'^MOVES = "(.*)"$', _STATE, re.M).group(1)
AGAIN = re.search(r'^AGAIN = "(.*)"$', _STATE, re.M).group(1)
CONSTANT_FORKS = int(re.search(r"^CONSTANT_FORKS = ([0-9]+)$", _STATE, re.M).group(1))


def _lost(what: str, s: dict) -> List[str]:
    fails = "its import fails"
    out = []
    for m, xs in sorted(s.get("lost", {}).items()):
        if xs and xs[0].startswith(fails):
            why = xs[0][len(fails):].strip(" ()")
            out.append(f"state: {what}: it no longer imports {m}, and {fails} there ({why}): "
                       f"what ran before it is gone (by hand)")
            continue
        home = [x[len(MOVES):] for x in xs if x.startswith(MOVES)]
        xs = [x for x in xs if not x.startswith(MOVES)]
        if xs:
            out.append(f"state: {what}: it no longer imports {m}, and importing {m} changes "
                       f"{_listed(xs)} (an import the code that needs it must make: by hand)")
        if home:
            who = s.get("unloaded", {}).get(m)
            lead = (f"state: {what}: {'its import' if who is None else _listed(who)} no longer "
                    f"load{'s' if who is None or len(who) == 1 else ''} {m}")
            if home[0] == AGAIN:
                out.append(f"{lead}, whose state may move with the environment ({AGAIN}): a test "
                           f"that imports it first may now give it its own (TJ-3933: by hand)")
            else:
                out.append(f"{lead}, whose state moves with the environment ({home[0]}): a test "
                           f"that imports it first now gives it its own (TJ-3869: by hand)")
    return out


# One importer's loads (TJ-3869, TJ-3905): the repository files, relative
# to the tree's root, that importing the test module NAME alone loads, run
# as its runner runs it: its FOLDER first on sys.path, after the hermetic
# setup when PRELOAD (tests/: the runner and discovery import it first; a
# package's tests run without it, scripts/ci.sh's fetch-plugin stage).
_LOADS = r"""
import importlib, importlib.util, json, os, sys
root, folder, name, preload, out = sys.argv[1:6]
real = os.path.realpath(root)
sys.path.insert(0, folder)
if preload == "1":
    spec = importlib.util.find_spec("_hermetic")       # this tree's, as the runner preloads it
    if spec is not None and os.path.realpath(spec.origin or "/").startswith(real + os.sep):
        importlib.import_module("_hermetic")
try:
    importlib.import_module(name)
except BaseException as e:
    json.dump({"error": f"{type(e).__name__}: {e}"[:300]}, open(out, "w"))
    sys.exit(0)
files = {os.path.relpath(os.path.realpath(f), real).replace(os.sep, "/")
         for f in (getattr(m, "__file__", None) for m in list(sys.modules.values()))
         if isinstance(f, str)}
json.dump({"files": sorted(f for f in files if f != ".." and not f.startswith("../"))},
          open(out, "w"))
"""

# The importer children run at once (TJ-3908).
LOAD_WORKERS = 5


def _test_folder(rel: str) -> str:
    """The test folder of REL (a path relative to the root): tests, or
    packages/P/tests; empty when it is in neither."""
    parts = rel.split("/")
    if parts[0] == "tests" and len(parts) > 1:
        return "tests"
    if parts[0] == "packages" and len(parts) > 3 and parts[2] == "tests":
        return "/".join(parts[:3])
    return ""


def importer_loads(tree: Path, home: Path, rel: str, out: Path) -> Optional[List[str]]:
    """The files of TREE (relative to its root) that importing the test
    module REL alone loads, run as its runner runs it (`_LOADS`): one of
    tests/ from tests/, after the hermetic setup; a package's from the
    root, its folder first, as `unittest discover -s` does. None when
    that import, or the child, fails."""
    folder = _test_folder(rel)
    parts = rel[len(folder) + 1:-len(".py")].split("/")
    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    core = folder == "tests"
    try:
        r = subprocess.run([sys.executable, "-B", "-c", _LOADS, str(tree), str(tree / folder), name,
                            "1" if core else "0", str(out)],
                           cwd=tree / "tests" if core else tree, env=_child_env(tree, home),
                           capture_output=True, text=True, timeout=TIMEOUT)
        got = json.loads(out.read_text(encoding="utf-8")) if not r.returncode else {}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        got = {}
    return got.get("files")


def importers(base: Path, source: str) -> List[str]:
    """The test modules of BASE (test_*.py of tests/ and each
    packages/*/tests, but SOURCE's own) whose imports reach
    tests/SOURCE.py through test modules, at module level, in a function,
    by `import_module` or `__import__` of a constant or a string naming
    one (TJ-3905, TJ-3935): its real importers, over-counted rather than
    missed, as paths relative to the root."""
    index = _TestIndex(base)
    target = f"tests/{source}.py"
    return sorted(f for f in index.files if f != target and f.rpartition("/")[2].startswith("test_")
                  and target in index.closure([f]))


def unloaded(base: Path, head: Path, home: Path, users: List[str],
             want: Dict[str, Optional[str]]) -> Dict[str, List[str]]:
    """For each of WANT (a module and its file, relative to the root),
    the test modules of USERS that no longer load it and may import it
    inside a test. Each is imported alone, in a child of its own on BASE
    and on HEAD (`importer_loads`, LOAD_WORKERS at once: TJ-3908); it no
    longer loads the module when BASE's import loads it and HEAD's does
    not (TJ-3907). It may then import it in a test: always a module of the
    product (which imports its own modules in functions), and a test
    helper when a file of a test folder HEAD's import loaded imports it
    inside a function or names it in a string, or so imports a helper
    that imports it, at module level or so again (`lazy_reach`). A
    child that fails counts as one whose BASE import loads it and whose
    HEAD import does not, and may import it, said with its name."""
    jobs = [(t, u) for u in users for t in (base, head)]
    for i in range(len(jobs)):
        (home / f"loads-{i}").mkdir()

    def one(i: int) -> Optional[List[str]]:
        tree, u = jobs[i]
        return importer_loads(tree, home / f"loads-{i}", u, home / f"loads-{i}.json")
    with concurrent.futures.ThreadPoolExecutor(max_workers=LOAD_WORKERS) as pool:
        got = dict(zip(jobs, pool.map(one, range(len(jobs)))))
    index = _TestIndex(head)
    out: Dict[str, List[str]] = {}
    for w, rel in want.items():
        helper = rel is not None and _test_folder(rel) != ""
        out[w] = []
        for u in users:
            was, now = got[(base, u)], got[(head, u)]
            if rel is not None and ((was is not None and rel not in was)
                                    or (now is not None and rel in now)):
                continue
            if not helper or now is None or rel in index.lazy_reach(now):
                failed = [side for side, x in (("BASE", was), ("HEAD", now)) if x is None]
                out[w].append(u + (f" (its import fails on {' and '.join(failed)})" if failed else ""))
    return out


class _TestIndex:
    """The modules of TREE's test folders (tests/ and each
    packages/*/tests), by their path relative to the root: what each
    imports at module level, and what it imports in a function or names
    in a string (a `mock.patch` target), found as its runner's sys.path
    finds a name: in its own folder first, then in tests/ (a package's
    tests/_bootstrap.py appends the core's) (TJ-3869, TJ-3905)."""
    def __init__(self, tree: Path):
        self.tree = tree
        self.files: Set[str] = {f.relative_to(tree).as_posix() for f in test_files(tree)}
        self._seen: Dict[str, Tuple[Set[str], Set[str]]] = {}

    def _in(self, d: str, parts: List[str]) -> Set[str]:
        """The files of folder D the dotted PARTS name: the module and each
        package above it."""
        out = set()
        for i in range(1, len(parts) + 1):
            p = "/".join([d, *parts[:i]])
            out |= {f for f in (p + ".py", p + "/__init__.py") if f in self.files}
        return out

    def _named(self, s: str, folder: str) -> Set[str]:
        """The files a dotted name or a string reaches from FOLDER: in the
        first folder that has its top-level name (a module, a package or a
        namespace one), itself and each package above it."""
        parts = s.split(".")
        for d in dict.fromkeys((folder, "tests")):
            top = f"{d}/{parts[0]}"
            if f"{top}.py" in self.files or any(f.startswith(top + "/") for f in self.files):
                return self._in(d, parts)
        return set()

    def _relative(self, rel: str, x: ast.ImportFrom) -> Set[str]:
        """The files a relative import of REL's names: its package, up
        LEVEL - 1, then the module and each name in it that is one."""
        d = rel.rpartition("/")[0].split("/")
        d = d[:len(d) - (x.level - 1)] if x.level > 1 else d
        mod = (x.module or "").split(".") if x.module else []
        here = "/".join(d + mod)
        return {f for f in (f"{here}.py", f"{here}/__init__.py",
                            *(f"{here}/{a.name}{e}" for a in x.names for e in (".py", "/__init__.py")))
                if f in self.files}

    def _read(self, rel: str) -> Tuple[Set[str], Set[str]]:
        if rel not in self._seen:
            top: Set[str] = set()
            lazy: Set[str] = set()
            try:
                tree = ast.parse((self.tree / rel).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                tree = ast.Module(body=[], type_ignores=[])
            folder = _test_folder(rel)
            inner = {id(x) for d in ast.walk(tree)
                     if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                     for x in ast.walk(d) if x is not d}
            for x in ast.walk(tree):
                reached: Set[str] = set()
                if isinstance(x, ast.Import):
                    for a in x.names:
                        reached |= self._named(a.name, folder)
                elif isinstance(x, ast.ImportFrom) and x.level:
                    reached = self._relative(rel, x)
                elif isinstance(x, ast.ImportFrom) and x.module:
                    for m in [x.module, *(f"{x.module}.{a.name}" for a in x.names)]:
                        reached |= self._named(m, folder)
                (lazy if id(x) in inner else top).update(reached)
                if isinstance(x, ast.Constant) and isinstance(x.value, str) \
                        and re.fullmatch(r"[\w.]+", x.value):
                    lazy |= self._named(x.value, folder)
            self._seen[rel] = (top - {rel}, lazy - {rel})
        return self._seen[rel]

    def closure(self, files: Iterable[str]) -> Set[str]:
        """FILES and what they import, at module level, in a function or
        by a string naming one, through each (TJ-3935)."""
        todo, reach = list(files), set()
        while todo:
            f = todo.pop()
            if f in self.files and f not in reach:
                reach.add(f)
                top, late = self._read(f)
                todo += sorted(top | late)
        return reach

    def lazy_reach(self, files: Iterable[str]) -> Set[str]:
        """The test modules the code of FILES may import inside a test:
        what they import in a function or name in a string, with what
        those import, in a function too (over-counted rather than
        missed: TJ-3935)."""
        return self.closure(m for f in files if f in self.files for m in self._read(f)[1])


def state_check(base: Path, head: Path, home: Path, table: List[Tuple[str, str, str]],
                td: Path) -> List[str]:
    """For each source: an importer's view (BASE's `import SOURCE` and
    HEAD's `import _support.SUPPORT` give the moved names the same values,
    and, once every module BASE's import loaded is loaded, the same rest; each
    of those HEAD's import did not load changes nothing when imported
    alone), and the source's own (`import SOURCE` on both: the same for
    the names HEAD's keeps, which lacks only moved names and the imports
    only they read). Two runs of BASE's own import that differ fail the
    check, as it cannot compare them. Each run has a HOME of its own."""
    # By exact name (TJ-3936), what the world and `lost_effects` leave out:
    # the support package, the hermetic setup, this batch's sources (what
    # their own imports do is the world's) and support modules. The replay
    # leaves out only the first and the last.
    skip = {"_support", "_hermetic", *(s for s, _m, _a in table),
            *(f"_support.{m}" for _s, m, _a in table)}
    envs = test_env(head)
    problems: List[str] = []
    runs = [0]

    def run(tree: Path, mod: str, names: List[str], tag: str, others=(),
            preload: Union[bool, str] = True) -> dict:
        runs[0] += 1
        h = home / f"state-{runs[0]}"
        h.mkdir()
        got = state(tree, h, mod, names, skip, td / f"state-{tag}.json", others, preload, envs)
        for k, vs in sorted((got.get("untried") or {}).items()):     # TJ-3978
            print(f"check: note: {tag}: a replay gave {k} {CONSTANT_FORKS} of the tests' "
                  f"constants (CONSTANT_FORKS), not {_listed(vs)}")
        if got.get("home_cut"):                                      # TJ-3981
            print(f"check: note: {tag}: what lies below HOME passed HOME_MAX entries; "
                  f"past them it is not compared")
        return got

    def loaded(a: dict) -> List[Tuple[str, Optional[str]]]:
        """What A's run loaded, in the order finished, each with its file."""
        files = a.get("files", {})
        return [(x, files.get(x)) for x in a.get("loaded", []) if x != "__main__"]

    def noisy(a: dict, tag: str, s: str, preload: Union[bool, str] = True) -> List[str]:
        noise = {k for k in state_diff(a, run(base, s, ["*"], tag, preload=preload))
                 if k[0] != "deep"}         # a walk cut short is said as that (TJ-3853)
        return [f"state: {s}: two runs of BASE's own import differ, so the check cannot "
                f"compare them: {_listed([':'.join(k[1:]) for k in sorted(noise)])} "
                f"(by hand)"] if noise else []
    for s, m, _a in table:
        moved = moved_names(head / SUPPORT / f"{m}.py")
        a = run(base, s, ["*"], f"{s}-base")
        others = loaded(a)
        b = run(head, f"_support.{m}", sorted(moved), f"{s}-sup", others)
        c = run(head, s, ["*"], f"{s}-head", others)
        homely = sorted(n for n, xs in b.get("lost", {}).items() if any(x.startswith(MOVES) for x in xs))
        if homely:      # only where a real importer may now import it in a test (TJ-3869)
            runs[0] += 1
            (home / f"state-{runs[0]}").mkdir()
            files = dict(others)
            users = unloaded(base, head, home / f"state-{runs[0]}", importers(base, s),
                             {n: files.get(n) for n in homely})
            for n in homely:
                if users[n]:
                    b.setdefault("unloaded", {})[n] = users[n]
                else:
                    b["lost"][n] = [x for x in b["lost"][n] if not x.startswith(MOVES)]
                    if not b["lost"][n]:
                        del b["lost"][n]
        mine = state_diff(a, b, moved)
        own = state_diff(a, c, set(c.get("names", {})) & set(a.get("names", {})))
        if "error" not in a and "error" not in c:
            # The imports only the moved names read are dropped (no importer
            # can read them: a re-export is refused).
            src_tree = ast.parse(MT.read_exact(base / "tests" / f"{s}.py"))
            imported = {x for st in src_tree.body if isinstance(st, (ast.Import, ast.ImportFrom))
                        for x in module_binds(st)}
            lost = sorted(set(a["names"]) - set(c["names"]) - moved - imported)
            if lost:
                problems.append(f"state: {s} on HEAD lacks {_listed(lost)}")
        if (mine or own) and "error" not in a:
            problems += noisy(a, f"{s}-base2", s)
        problems += _say(f"an importer of {s}'s moved names", mine)
        problems += _lost(f"an importer of {s}'s moved names", b)
        problems += _say(s, own)
        problems += _lost(s, c)
        # Run alone (`python -m unittest SOURCE`): no preload, its own imports
        # give it the hermetic setup, in its own order (TJ-3845).
        a0 = run(base, s, ["*"], f"{s}-base-alone", preload=False)
        c0 = run(head, s, ["*"], f"{s}-head-alone", loaded(a0), preload=False)
        alone, mode = f"{s} run alone", False
        if "error" not in a0 and "error" not in c0 and a0["hermetic"] != c0["hermetic"]:
            if a0["hermetic"]:
                problems.append(f"state: {alone}: HEAD's import no longer runs the hermetic "
                                f"setup (tests/_hermetic: a synthetic HOME)")
                continue
            # HEAD's import runs the setup and BASE's did not (TJ-3969): BASE's
            # import then the setup, against HEAD's, each HOME by one name.
            print(f"check: note: {alone}: HEAD's import runs the hermetic setup, BASE's "
                  f"did not (a synthetic HOME): compared with BASE's import followed by "
                  f"the setup, the two HOMEs as one")
            mode = "endm"
            a0 = run(base, s, ["*"], f"{s}-base-alone-m", preload=mode)
            c0 = run(head, s, ["*"], f"{s}-head-alone-m", loaded(a0), preload="0m")
        own0 = state_diff(a0, c0, set(c0.get("names", {})) & set(a0.get("names", {})))
        if own0 and "error" not in a0:
            problems += noisy(a0, f"{s}-base-alone2", s, preload=mode)
        problems += _say(alone, own0)
        problems += _lost(alone, c0)
    return problems


def test_ids(tree: Path, home: Path, td: Path, tag: str) -> Dict[str, dict]:
    """The ids of each test tree in TREE: tests/ and each packages/*/tests."""
    got = {}
    for tests in [tree / "tests", *sorted(tree.glob("packages/*/tests"))]:
        rel = tests.relative_to(tree).as_posix()
        out = td / f"ids-{tag}-{rel.replace('/', '-')}.json"
        extra = str(tests.parent / "src") if tests.parent != tree else ""
        subprocess.run([sys.executable, "-B", "-c", _IDS, str(out)], cwd=tests,
                       env=_child_env(tree, home, extra), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        got[rel] = json.loads(out.read_text(encoding="utf-8"))
    return got


def ids_check(base: Path, head: Path, home: Path, td: Path) -> List[str]:
    """The ids stage: discovery in both trees gives the same test ids, each
    with the same markers and skip, on its function and its class (TJ-3946:
    a moved fixture base's class-level `@rule` or skip reaches its
    subclasses' tests as before), and loads every module."""
    problems: List[str] = []
    got = {"base": test_ids(base, home, td, "base"), "head": test_ids(head, home, td, "head")}
    for name in ("base", "head"):
        for rel, g in got[name].items():
            for e in g["errors"]:
                problems.append(f"ids: {name} fails to load a module in {rel}: {e}")
    rels = sorted(set(got["base"]) | set(got["head"]))
    for rel in rels:
        b = sorted(got["base"].get(rel, {}).get("ids", []))
        h = sorted(got["head"].get(rel, {}).get("ids", []))
        if b != h:
            miss, extra = sorted(set(b) - set(h)), sorted(set(h) - set(b))
            problems.append(f"ids: {rel}: {len(miss)} of BASE's missing, {len(extra)} "
                            f"new: {(miss + extra)[:6]}")
        marks: Dict[str, Dict[str, List[str]]] = {"base": {}, "head": {}}
        for name in marks:
            for i, *how in got[name].get(rel, {}).get("marks", []):
                marks[name].setdefault(i, []).append(json.dumps(how))
        differ = sorted(i for i in set(marks["base"]) & set(marks["head"])
                        if sorted(marks["base"][i]) != sorted(marks["head"][i]))
        if differ:
            problems.append(f"ids: {rel}: {len(differ)} test(s) whose markers or skip differ "
                            f"(`__tax_rules__`, `__unittest_skip__`): {differ[:6]}")
    print("check: ids: " + "; ".join(
        f"{rel} {len(got['head'].get(rel, {}).get('ids', []))} in HEAD, "
        f"{len(got['base'].get(rel, {}).get('ids', []))} in BASE" for rel in rels))
    return problems


def record_baseline(work: Path, home: Path) -> Optional[str]:
    """BASE's check_architecture records the baseline on the work tree
    (TJ-3822): HEAD's must be it, byte for byte. None when BASE has none."""
    tool = work / "scripts" / "check_architecture.py"
    if not tool.is_file():
        return None
    r = subprocess.run([sys.executable, "-B", str(tool), "--accept-growth=ruff,noqa"],
                       cwd=work, env=_child_env(work, home), capture_output=True, text=True)
    return None if r.returncode == 0 else (r.stdout + r.stderr).strip()[-300:] or "failed"


def check(repo: Path, base_rev: str, head_rev: str, allow: List[str], no_ids: bool) -> int:
    VT = _vt()
    problems: List[str] = []
    failed = "check: FAILED (the text check; nothing of HEAD's was run)"
    with tempfile.TemporaryDirectory(prefix="extract-check-") as tdn:
        td = Path(tdn)
        base, head, work, home = (td / n for n in ("base", "head", "work", "home"))
        for d in (base, head, home):
            d.mkdir()

        # 1. text
        try:
            base_e = materialize(repo, base_rev, base)
            head_e = materialize(repo, head_rev, head)
            table = table_of(base, head)
        except Refused as e:
            print(f"check: {e}\n{failed}")
            return 1
        if not table:
            print("check: HEAD adds no tests/_support/ module: no batch", file=sys.stderr)
            return 2
        shutil.copytree(base, work, symlinks=True)
        tf = td / "table.txt"
        tf.write_text("".join(f"{s} {m} {a}\n" for s, m, a in table), encoding="utf-8")
        env = {k: v for k, v in VT._genv().items() if k != "PYTHONPATH"}
        r = subprocess.run([sys.executable, "-I", "-S", "-B",
                            str(work / "scripts" / "extract_support.py"), str(tf),
                            f"--repo={work}"], cwd=td, env=env, capture_output=True, text=True)
        print(f"check: {len(table)} source(s): "
              f"{', '.join(f'{s} -> {m}' for s, m, _a in table)}")
        if r.returncode:
            print(f"check: BASE's tool refused HEAD's table: {(r.stdout + r.stderr).strip()}")
            print(failed)
            return 1
        work0 = work_entries(repo, work)
        written = {f for f in set(work0) | set(base_e) if work0.get(f) != base_e.get(f)}
        why = record_baseline(work, home)
        work_e = work_entries(repo, work)
        diff = sorted(f for f in set(work_e) | set(head_e) if work_e.get(f) != head_e.get(f))
        hand = [f for f in diff if f in allow and f.endswith(".md") and f in head_e
                and "tests/" not in f and (f not in work_e or work_e[f][0] == head_e[f][0])]
        bad = [f"text: {f} is not what BASE's tool writes from BASE" for f in diff
               if f not in hand]
        if why:
            bad.append(f"baseline: BASE's check_architecture fails on the work tree: {why}")
        how = "the baseline as BASE's check_architecture records it" \
            if (work / "scripts" / "check_architecture.py").is_file() \
            else "no check_architecture in BASE to record a baseline"
        print(f"check: text: the tool wrote {len(written)} file(s), {how}; beyond it, by hand: "
              f"{hand or 'none'}")
        if bad:
            for p in bad:
                print(f"check: {p}")
            print(failed)
            return 1

        # 2. state: HEAD's code is BASE's tool's output, byte for byte.
        problems += state_check(base, head, home, table, td)
        print(f"check: state: {len(table)} source(s), an importer's view and each source's")

        # 3. ids
        if not no_ids:
            problems += ids_check(base, head, home, td)
    for p in problems:
        print(f"check: {p}")
    print("check: " + ("FAILED" if problems else "HEAD is BASE's tool run on BASE"))
    return 1 if problems else 0


def run_base_copy(repo: Path, base: str, head: str, flags: List[str]) -> int:
    """Run BASE's copy of this script: a batch cannot change its own proof."""
    VT = _vt()
    with tempfile.TemporaryDirectory(prefix="extract-check-tools-") as td:
        try:
            VT.export(base, Path(td), repo, TOOLS)
        except subprocess.CalledProcessError:
            print(f"check: BASE lacks one of {', '.join(TOOLS)}: the tool lands in a commit "
                  f"of its own before a batch", file=sys.stderr)
            return 2
        me = Path(td) / "scripts" / "extract_support.py"
        env = {k: v for k, v in VT._genv().items() if k != "PYTHONPATH"}
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(me), "--check", base, head,
                               f"--repo={repo}", "--base-copy", *flags], env=env).returncode


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo, allow, args, flags = REPO, [], [], []
    for a in argv:
        if a.startswith("--repo="):
            repo = Path(a[len("--repo="):]).resolve()
        elif a.startswith("--allow="):
            allow += [x for x in a[len("--allow="):].split(",") if x]
        elif a in ("--check", "--dry-run", "--no-ids", "--trees", "--base-copy"):
            flags.append(a)
        else:
            args.append(a)
    usage = __doc__.split("\n\n")[1]
    try:
        if "--check" in flags:
            if len(args) > 2 or any(a.startswith("--") for a in args) or "--dry-run" in flags:
                print(usage, file=sys.stderr)
                return 2
            VT = _vt()
            bad = VT.rewritten_history(repo)
            if bad:
                print(f"check: {bad}: git would not show the objects themselves", file=sys.stderr)
                return 2
            head, hkind = VT.resolve(repo, args[1] if len(args) > 1 else "HEAD")
            base, bkind = VT.resolve(repo, args[0] if args else VT.default_base(repo, head))
            if "--trees" not in flags:
                if "tree" in (bkind, hkind):
                    print("check: BASE and HEAD are commits (--trees: a synthetic "
                          "repository's trees)", file=sys.stderr)
                    return 2
                bad = VT.ancestry_problem(repo, base, head)
                if bad:
                    print(f"check: {bad}", file=sys.stderr)
                    return 2
            rest = [f"--allow={','.join(allow)}"] if allow else []
            rest += [f for f in flags if f in ("--no-ids", "--trees")]
            if "--base-copy" not in flags:
                return run_base_copy(repo, base, head, rest)
            return check(repo, base, head, allow, "--no-ids" in flags)
        if len(args) != 1 or set(flags) - {"--dry-run"} or args[0].startswith("--"):
            print(usage, file=sys.stderr)
            return 2
        table = read_table(Path(args[0]).read_text(encoding="utf-8"))
        return extract(repo, table, "--dry-run" in flags)
    except Refused as e:
        print(f"extract_support: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

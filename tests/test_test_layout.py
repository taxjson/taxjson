"""The layout of the test suite itself (PLAN.md step 9: the round-named
modules move into packages below tests/).

- No module below tests/ (a test or a helper) computes a folder from its
  own file: `__file__` depth paths (`Path(__file__).resolve().parent.parent`)
  name the wrong folder once the module moves, and a child process given
  that folder's src would import the venv's editable install instead of
  this tree. tests/_paths.py names the folders (REPO, SRC, TESTS, EXAMPLES,
  FIXTURES); the sitecustomize shims keep theirs (they run in a child
  before tests/ is on its path, and find the shim chain beside them).
- Every folder below tests/ that holds a test module is a package, as is
  each folder above it: unittest's discovery and scripts/run_tests_parallel.py
  skip a folder without `__init__.py` silently, its tests never run.
- The round-named modules (named after the audit or fix round that wrote
  them, not the behaviour they pin) only fall in number: step 9 moves them
  by rule family and feature; done below 50.
- The modules (test modules and their helpers) that import a test
  module, or load one by name or path, only fall in number: a helper two
  modules share lives in tests/_support/ (step 9 B1), so a module moves
  without breaking another. No module binds, at its module scope, a
  TestCase class with tests of its own (or through a base in its module)
  that another test module defines (unittest would run its tests in
  both), and no helper holds one: a quick check of the source; the
  parallel runner's collect checks the classes themselves (a class with
  tests, inherited ones too, bound where it is not defined). The quick
  check follows a base from-imported from tests/_support: a class whose
  tests come from a support mixin holds tests. A class built on a
  support mixin lists the mixin before `unittest.TestCase` (else
  TestCase's empty setUp and setUpClass win), and a class whose tests
  come from a mixin carries no class-level `@rule`, `@rule_absent` or
  skip: they go on the mixin (TJ-3947). A support mixin of tests (a
  class there with tests, no TestCase) is named `<Subject>Cases`
  (TJ-3988).
- Each module of tests/_support/ is named for its subject: its name
  starts with one of `SUPPORT_PREFIXES` (a broker or format, a country,
  or a section of docs/architecture-map.md; TJ-3999).
- Each test id another test re-runs in a child process by name
  (tests/test_temp_isolation.py's `_AFFECTED`) is a test discovery finds
  in the source tree: a renamed or moved test fails here, not as a
  child's import error (TJ-3949).
- A test module run alone (`cd tests && python -m unittest test_x`) gets
  the synthetic HOME (tests/_hermetic) before it loads a module that reads
  HOME at its import (lib/rate_cache's and lib/crypto_prices' CACHE_FILE):
  else that module binds the HOME it was given, a developer's own, and
  its cache (TJ-3857). Each test module whose module-scope imports reach
  one is imported alone in a fresh interpreter to check it.
- No module of the test trees uses `os.environ` at its import but to
  read it (`environ_at_import`): a copy taken there, or a change made
  there, leaks across the tests of its process (TJ-3951, TJ-3952). The
  setup's record of the environment it leaves (`_hermetic.ENVIRON`)
  aside. A function the import calls is not seen: tests/_tmpfiles.py's
  `isolate_layouts()`, and the plugin's test bootstrap's `_setup()`
  (packages/taxjson-fetch/tests/_bootstrap.py, which puts the plugin on
  PYTHONPATH)."""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _paths


def _own_file_uses(text):
    """Line numbers where a module names its own file: the names
    `__file__` and `__spec__`, `sys.modules[__name__]`, a `"__file__"`
    subscript (`globals()["__file__"]`); not another module's attribute
    (`taxjson.__file__`), not a string. Over the syntax tree, so the
    same on every Python (an f-string's fields too, before 3.12).
    Beyond its reach, left to review (none in tests/ on 2026-11-14):
    `__loader__`, `__cached__`, `.get("__file__")`, `.get(__name__)`, an
    alias of `sys.modules`, `find_spec(__name__)`, `files(__package__)`;
    and it flags any `"__file__"` subscript and `__spec__.name`."""
    lines = set()
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, ast.Name) and n.id in ("__file__", "__spec__"):
            lines.add(n.lineno)
        elif isinstance(n, ast.Subscript):
            s = n.slice
            if (isinstance(s, ast.Name) and s.id == "__name__"
                    and isinstance(n.value, ast.Attribute)
                    and n.value.attr == "modules"):
                lines.add(n.lineno)
            elif isinstance(s, ast.Constant) and s.value == "__file__":
                lines.add(n.lineno)
    return sorted(lines)


# The modules that name their own file, by path below tests/: the one
# that names the folders, and the sitecustomize shims (see the docstring).
_OWN_FILE_OK = ("_paths.py", "_clockshift/sitecustomize.py", "_oldsum/sitecustomize.py",
                "_opentrace/sitecustomize.py", "_shown_log/sitecustomize.py")


def _python_files():
    return sorted(p for p in _paths.TESTS.rglob("*.py")
                  if "__pycache__" not in p.parts
                  and p.relative_to(_paths.TESTS).as_posix() not in _OWN_FILE_OK)


# The round-named modules left (PLAN.md step 9; 513 on 2026-11-14): named
# after a fix round (test_fix_*, test_fixl_*, test_fixd3_*), an audit or a
# decisions round. A batch that moves some lowers it to the new count.
ROUND_NAMED_MAX = 0
_ROUND_NAMED = re.compile(r"test_(fix(?:[a-z]\d*)?|audit|decisions)_\w*\.py\Z")
# Named after a round in other words (TJ-3671): a review, an audit, its
# fixes or bugs, a refactor step, a dated pass.
_ROUND_WORDS = re.compile(
    r"test_(\w*_)?(fixes|followups|bugs|review|audit|polish_pass|refactor_step\d+)(_\w*)?\.py\Z"
    r"|test_\w*_20\d\d_\d\d[a-z]?\.py\Z")
# Named after their subject: the taxjson-audit tool, `tjs slip-audit`,
# the audit command's reads through model/result.
_NOT_ROUND_NAMED = ("test_audit_command.py", "test_slip_audit.py", "test_slip_audit_cra.py",
                    "test_model_result_audit.py")


# The modules below tests/ or a package's tests/ (test modules and their
# helpers) that import a test_* module or load one by name or path (PLAN.md
# step 9 B1: 257 on 2026-11-14). B1 moves what they share into
# tests/_support/; a batch that does lowers it to the new count, and B1
# ends at 0.
CROSS_IMPORTS_MAX = 0
# The calls that load a module by name or path.
_LOADERS = ("spec_from_file_location", "import_module", "__import__", "run_path",
            "run_module", "SourceFileLoader")
# The prefixes a tests/_support/ module's name starts with (step 9 B1z,
# TJ-3999), so a reader finds a helper by its subject. A new module takes
# one; a new prefix goes here, in its group.
SUPPORT_PREFIXES = (
    # a broker, a file format, or several of them (qt_ Questrade, wb_
    # Webull, cb_ Coinbase, tt_ the .tt books, crypto_ the crypto exchanges
    # together, parser_ the parse stage over several brokers)
    "ib_", "qt_", "rbc_", "wb_", "cb_", "kraken_", "crypto_", "generic_", "tt_", "parser_",
    # a country's rules and forms
    "ca_", "us_",
    # a section of docs/architecture-map.md (suite_: the test suite itself)
    "engine_", "corp_", "fx_", "transfer_", "symbols_", "planning_", "filing_", "years_",
    "view_", "model_", "audit_", "partition_", "config_", "cli_", "redact_", "style_",
    "release_", "suite_")
# The feature areas (PLAN.md step 9, B11 to B14): a module whose tests no
# tax-logic rule states goes to tests/features/<area>/, one area per
# section of docs/architecture-map.md (the three parser sections share
# parsers/). A new section gets an area here.
FEATURE_AREAS = {
    "cli": ("The CLI and command dispatch",),
    "tools": ("Stand-alone tools and in-process dispatch",),
    "run": ("The tjs run orchestrator",),
    "discovery": ("Input discovery and broker detection",),
    "parse": ("The parse stage",),
    "parsers": ("Broker parsers: shared base and schema", "Equity broker parsers", "Crypto parsers"),
    "generic_import": ("Generic CSV, hand entry (.tt) and adding a parser",),
    "merge": ("Merge, sort, dedup and validate",),
    "ticker_map": ("ticker.map, renames, cross-listings and symbol codes",),
    "corporate_actions": ("Corporate actions and elections",),
    "fx": ("Currency conversion and FX rates",),
    "distributions": ("Fund distributions and income dating",),
    "transfers": ("Transfers, crypto sends and opening balances",),
    "missing_history": ("Missing purchase history",),
    "model": ("The model: rows, records and money",),
    "engines": ("The gains engines",),
    "gains_run": ("The gains run and the wash passes",),
    "reports": ("Per-account reports",),
    "output": ("Output style and messages",),
    "listings": ("Row listings",),
    "totals": ("Totals and positions",),
    "summaries": ("Summaries and estimates",),
    "checks": ("Checks before filing",),
    "explain": ("Audit, wash sales, explain and tax logic",),
    "planning": ("Planning before you trade",),
    "forms": ("Filing forms and slips",),
    "compare": ("Comparing runs",),
    "lock": ("Filed-year lock and year hand-off",),
    "redact": ("Redact",),
    "configuration": ("Configuration: taxjson.toml, init, format and migrate",),
    "year_folders": ("One folder of exports for every year",),
    "partition": ("The Canada and USA partition",),
    "calendars": ("Markets, calendars and dates",),
    "fetch": ("The fetch plugin",),
    "install": ("Release channels and install",),
    "repo_checks": ("The CI gate and repository checks",),
    "suite": ("Tests",),
}


def _test_trees():
    return [_paths.TESTS] + sorted((_paths.REPO / "packages").glob("*/tests"))


def _all_test_tree_files(trees=None):
    return sorted(p for tree in (_test_trees() if trees is None else trees) for p in tree.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _call_name(f):
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _strings(node):
    return [x.value for x in ast.walk(node)
            if isinstance(x, ast.Constant) and isinstance(x.value, str)]


def _patch_kind(f):
    """`patch`, `mock.patch` and `unittest.mock.patch`, `patch.dict`,
    `patch.multiple` (their target, a dotted name, imports its module: the
    name's module, or for `multiple` the name itself); None for any other
    call (`client.patch`, `patch.object`)."""
    if isinstance(f, ast.Name):
        return "patch" if f.id == "patch" else None
    if isinstance(f, ast.Attribute):
        if f.attr == "patch":
            v = f.value
            mock = (isinstance(v, ast.Name) and v.id == "mock") or \
                (isinstance(v, ast.Attribute) and v.attr == "mock")
            return "patch" if mock else None
        if f.attr in ("dict", "multiple") and _patch_kind(f.value):
            return f.attr
    return None


def _patch_target(n):
    args = [*n.args[:1], *(k.value for k in n.keywords if k.arg in ("target", "in_dict"))]
    for a in args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
    return None


def cross_imports(path, stems):
    """The test modules (by STEMS, their file names' stems) that the module
    at PATH imports, at any depth (`import test_x`, `from test_x import y`,
    `from pkg import test_x`, `from . import test_x`, `from .test_x import
    y`), names as a patch target (`mock.patch("test_x.y")`), or loads by
    name or path (a `_LOADERS` call whose arguments, or the strings a name
    among them is assigned, name one); its own name aside. Not counted: a
    name built at run time (an f-string, a concatenation) and a child
    process's command line."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned = defaultdict(list)
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name):
                    assigned[tg.id].extend(_strings(n.value))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            names = ([n.module] + [f"{n.module}.{a.name}" for a in n.names] if n.module
                     else [a.name for a in n.names])
        elif isinstance(n, ast.Call) and _patch_kind(n.func) and _patch_target(n):
            segs = _patch_target(n).split(".")
            names = segs if _patch_kind(n.func) == "multiple" else segs[:-1]
        elif isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.Call) and _call_name(n.func) in _LOADERS:
            names = []
            for a in [*n.args, *(k.value for k in n.keywords)]:
                names += _strings(a) + [s for x in ast.walk(a) if isinstance(x, ast.Name)
                                        for s in assigned.get(x.id, ())]
            names = [re.split(r"[/\\]", s)[-1].removesuffix(".py") for s in names]
        else:
            continue
        out.update(m.split(".")[-1] for m in names if m.split(".")[-1] in stems)
    out.discard(path.stem)
    return out


def _tree_of(p, trees):
    return next(t for t in trees if t in p.parents)


def _support_file(tests, module):
    """The file of `module` (`_support` or `_support.x`, a from-import's)
    below TESTS, a test tree; None for another module or none there."""
    parts = module.split(".")
    if tests is None or parts[0] != "_support":
        return None
    base = Path(tests).joinpath(*parts)
    return next((f for f in (base.with_suffix(".py"), base / "__init__.py") if f.is_file()), None)


def unprefixed_support(support):
    """The modules of SUPPORT (tests/_support/: each .py file but
    `__init__.py`, each folder but `__pycache__`) whose name starts with
    none of SUPPORT_PREFIXES."""
    names = sorted(p.stem if p.is_file() else p.name for p in Path(support).iterdir()
                   if (p.is_file() and p.suffix == ".py" and p.name != "__init__.py")
                   or (p.is_dir() and p.name != "__pycache__"))
    return [n for n in names if not n.startswith(SUPPORT_PREFIXES)]


class _Classes:
    """The classes one module's top level binds, by the name it binds
    them: its own, and those it from-imports from tests/_support (TESTS,
    its test tree; followed into the support module, and from there into
    the support modules it imports from). `support`: the module is below
    tests/_support (TJ-3947)."""

    def __init__(self, tree, tests, support=False, cache=None):
        self.tests, self.support = tests, support
        self.cache = {} if cache is None else cache
        self.own = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
        self.imported = {}
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                f = _support_file(tests, n.module)
                for a in n.names if f is not None else ():
                    if a.name != "*":
                        self.imported[a.asname or a.name] = (f, a.name)

    def resolve(self, name, seen=()):
        """(ClassDef, the _Classes of its module) that NAME, a base's
        source text, names here; None for a class from anywhere else."""
        if name in self.own:
            return self.own[name], self
        if name not in self.imported or self.imported[name] in seen:
            return None
        f, real = self.imported[name]
        if f not in self.cache:
            self.cache[f] = _Classes(ast.parse(f.read_text(encoding="utf-8")), self.tests,
                                     True, self.cache)
        return self.cache[f].resolve(real, seen + (self.imported[name],))

    def bases(self, c):
        """(base text, (ClassDef, _Classes) or None) for each of C's bases."""
        return [(ast.unparse(b), self.resolve(ast.unparse(b))) for b in c.bases]

    def is_case(self, c, seen=()):
        """C is a TestCase: a base named *TestCase, or one of its module's
        or a support module's that is one."""
        for s, r in self.bases(c):
            if s.endswith("TestCase") or (r is not None and r[0] not in seen
                                          and r[1].is_case(r[0], seen + (c,))):
                return True
        return False

    def own_tests(self, c):
        return [f.name for f in c.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                and f.name.startswith("test")]

    def support_tests(self, c, seen=()):
        """The test methods C gets from a base defined in tests/_support
        (a mixin's, and the mixins it is built on)."""
        out = []
        for _s, r in self.bases(c):
            if r is not None and r[1].support and r[0] not in seen:
                out += r[1].own_tests(r[0]) + r[1].support_tests(r[0], seen + (c,))
        return out

    def test_methods(self, c, seen=()):
        """Every test method C has: its own, and its bases' here or in a
        support module (what discovery finds on it)."""
        out = set(self.own_tests(c))
        for _s, r in self.bases(c):
            if r is not None and r[0] not in seen:
                out |= r[1].test_methods(r[0], seen + (c,))
        return out


def test_case_classes(tree, tests=None, support=False):
    """The TestCase classes with test methods TREE's module defines at its
    top level (a base that is a TestCase, or a class of its own that is
    one): methods of their own, or a tests/_support mixin's they are built
    on (TESTS, the module's test tree, to read it; SUPPORT: the module is
    one of tests/_support, its own classes mixins too; TJ-3947)."""
    scope = _Classes(tree, tests, support)
    return {name for name, c in scope.own.items() if scope.is_case(c) and (
        scope.own_tests(c) or scope.support_tests(c))}


# The class decorators that must go on a support mixin, not on the class
# built on it: a class-level marker wraps and marks the class's own tests
# only (tests/tax_rules `_apply`), and a skip skips this class but not
# another built on the same mixin.
_MIXIN_DECORATORS = ("rule", "rule_absent", "skip", "skipIf", "skipUnless")


def support_mixin_problems(files, trees):
    """Where a test module of FILES (below TREES) builds a TestCase class
    on a tests/_support mixin (a class there that is no TestCase) wrongly
    (TJ-3947): `path:line: Class: why`. It lists `TestCase` before the
    mixin (TestCase's empty setUp and setUpClass come first in its
    method order: the mixin's fixture silently gone), or its tests come
    from a mixin and it carries a class-level `@rule`, `@rule_absent` or
    skip (`_MIXIN_DECORATORS`)."""
    out = []
    for p in files:
        if not p.name.startswith("test_"):
            continue
        rel = p.relative_to(trees[0].parent).as_posix()
        scope = _Classes(ast.parse(p.read_text(encoding="utf-8")), _tree_of(p, trees))
        for c in scope.own.values():
            if not scope.is_case(c):
                continue
            bases = scope.bases(c)
            mixins = [(i, s) for i, (s, r) in enumerate(bases)
                      if r is not None and r[1].support and not r[1].is_case(r[0])]
            first_case = next((i for i, (s, _r) in enumerate(bases) if s.endswith("TestCase")),
                              None)
            for i, s in mixins:
                if first_case is not None and first_case < i:
                    out.append(f"{rel}:{c.lineno}: {c.name}: {bases[first_case][0]} before "
                               f"the support mixin {s}")
            if scope.support_tests(c):
                for d in c.decorator_list:
                    name = _call_name(d.func if isinstance(d, ast.Call) else d)
                    if name in _MIXIN_DECORATORS:
                        out.append(f"{rel}:{d.lineno}: {c.name}: a class-level @{name} on a "
                                   f"class whose tests come from a support mixin")
    return out


def misnamed_support_mixins(files, trees):
    """The mixins of tests in tests/_support among FILES (below TREES): a
    class there, no TestCase, with test methods of its own or from a base
    it is built on (what discovery finds on a TestCase built on it), whose
    name does not end in `Cases` (`<Subject>Cases`, TJ-3988):
    `path:line: Class`."""
    out = []
    for p in files:
        tests = _tree_of(p, trees)
        if not _in_support(p, tests):
            continue
        scope = _Classes(ast.parse(p.read_text(encoding="utf-8")), tests)
        out += [f"{p.relative_to(trees[0].parent).as_posix()}:{c.lineno}: {c.name}"
                for c in scope.own.values() if not c.name.endswith("Cases")
                and not scope.is_case(c) and scope.test_methods(c)]
    return out


def child_run_ids(path):
    """The test ids the module at PATH re-runs in a child process by name:
    its module-level `_AFFECTED` (tests/test_temp_isolation.py), read from
    its source."""
    for n in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_AFFECTED"
                                             for t in n.targets):
            return list(ast.literal_eval(n.value))
    return []


def undiscovered(test_id, tests):
    """Why unittest's discovery of TESTS (a test tree) finds no TEST_ID
    (`module.Class.method`, the module dotted below TESTS), read from the
    source; None when it finds it: the module a test_*.py file in package
    folders, the class one of its TestCase classes with tests
    (`test_case_classes`), the method one of the class's test methods
    (its own, or its bases' in the module or a support module) (TJ-3949)."""
    parts = test_id.split(".")
    if len(parts) < 3:
        return f"{test_id}: not module.Class.method"
    *mod, cls, meth = parts
    path = Path(tests).joinpath(*mod).with_suffix(".py")
    if not path.is_file():
        return f"{test_id}: no {'/'.join(mod)}.py"
    if not path.name.startswith("test_"):
        return f"{test_id}: {path.name} is not a test module"
    for d in path.parents:
        if d == Path(tests):
            break
        if not (d / "__init__.py").is_file():
            return f"{test_id}: {d.name}/ is not a package"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if cls not in test_case_classes(tree, tests):
        return f"{test_id}: no TestCase class {cls} with tests"
    scope = _Classes(tree, tests)
    if meth not in scope.test_methods(scope.own[cls]):
        return f"{test_id}: no test method {meth}"
    return None


def _never_imported(n):
    """`if TYPE_CHECKING:` and `if __name__ == "__main__":`: a body no
    test run executes at import."""
    if not isinstance(n, ast.If):
        return False
    s = ast.unparse(n.test).replace("'", '"')
    return s.endswith("TYPE_CHECKING") or s == '__name__ == "__main__"'


def _module_scope(tree):
    """TREE's statements that run at module scope: its body, and what an
    if, try, with or loop there holds; never a def's or a class's, nor a
    TYPE_CHECKING or `__main__` block's."""
    out, todo = [], list(tree.body)
    while todo:
        n = todo.pop(0)
        out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                or _never_imported(n):
            continue
        for _field, value in ast.iter_fields(n):
            for x in value if isinstance(value, list) else ():
                if isinstance(x, ast.stmt):
                    todo.append(x)
                elif isinstance(x, (ast.ExceptHandler, getattr(ast, "match_case", ()))):
                    todo.extend(x.body)
    return out


def bound_test_classes(files, trees):
    """Where a module of FILES (below TREES, the core's first) binds at its
    module scope a TestCase class with tests that another test module
    defines (unittest runs its tests there too): `path:line: module.Class`.
    By a from-import (relative, star or in an if or try too) or an
    assignment of `module.Class`; a stem is found in the importer's own
    tree first, as its run's sys.path has it."""
    by_tree = {t: {} for t in trees}
    for p in files:
        if p.name.startswith("test_"):
            by_tree[_tree_of(p, trees)].setdefault(p.stem, p)

    def find(importer, stem):
        return by_tree[_tree_of(importer, trees)].get(stem) or by_tree[trees[0]].get(stem)
    cache = {}

    def cases(p):
        if p not in cache:
            cache[p] = test_case_classes(ast.parse(p.read_text(encoding="utf-8")),
                                         _tree_of(p, trees))
        return cache[p]
    out = []
    for p in files:
        rel = p.relative_to(trees[0].parent).as_posix()
        mods = {}
        for n in _module_scope(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom):
                stem = (n.module or "").split(".")[-1]
                src = find(p, stem) if stem else None
                for a in n.names:
                    if src is not None and src != p and a.name == "*" and cases(src):
                        out += [f"{rel}:{n.lineno}: {stem}.{c}" for c in sorted(cases(src))]
                    elif src is not None and src != p and a.name in cases(src):
                        out.append(f"{rel}:{n.lineno}: {stem}.{a.name}")
                    elif find(p, a.name) not in (None, p):
                        mods[a.asname or a.name] = find(p, a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    src = find(p, a.name.split(".")[-1])
                    if src not in (None, p) and (a.asname or "." not in a.name):
                        mods[a.asname or a.name] = src
            elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                # A name bound to the class (`X = m.TestY`, or unpacked from
                # a tuple), never a list or tuple that holds it.
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                unpacked = any(isinstance(tg, (ast.Tuple, ast.List)) for tg in targets)
                vals = n.value.elts if unpacked and isinstance(n.value, (ast.Tuple, ast.List)) \
                    else [n.value]
                for v in vals:
                    if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                            and v.value.id in mods and v.attr in cases(mods[v.value.id]):
                        out.append(f"{rel}:{n.lineno}: {mods[v.value.id].stem}.{v.attr}")
    return out


def _in_support(p, tests):
    """P is a module of tests/_support (the package or a module of it)."""
    return p.relative_to(tests).parts[0] in ("_support", "_support.py")


def held_test_cases(files, root, trees=None):
    """The helpers of FILES (any module not named test_*) that hold a
    TestCase class with tests: `path: Class, ...` (a path below ROOT;
    TREES, the test trees, to follow a tests/_support base)."""
    return [f"{p.relative_to(root).as_posix()}: {', '.join(sorted(c))}"
            for p in files if not p.name.startswith("test_")
            for tests in [_tree_of(p, trees) if trees else None]
            for c in [test_case_classes(ast.parse(p.read_text(encoding="utf-8")), tests,
                                        tests is not None and _in_support(p, tests))] if c]


def _round_named(name):
    return bool(_ROUND_NAMED.match(name) or _ROUND_WORDS.match(name)) \
        and name not in _NOT_ROUND_NAMED


def _scope_names(n):
    """The names N, a module-scope statement, binds there: a def's or a
    class's name, an import's, and every name stored outside a nested def,
    class or lambda (a target, a walrus, an `except ... as`, a match
    capture)."""
    out, todo = [], [n]
    while todo:
        x = todo.pop()
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(x.name)     # its body is its own; what heads it runs here
            a = getattr(x, "args", None)
            todo.extend([*x.decorator_list, *getattr(x, "bases", ()),
                         *(a.defaults if a else ()), *(d for d in (a.kw_defaults if a else ()) if d),
                         *(k.value for k in getattr(x, "keywords", ()))])
            continue
        if isinstance(x, ast.Lambda):
            continue
        if isinstance(x, (ast.Import, ast.ImportFrom)):
            out += [a.asname or a.name.split(".")[0] for a in x.names]
        elif isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            out.append(x.id)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            out.append(x.name)
        elif type(x).__name__ in ("MatchAs", "MatchStar") and getattr(x, "name", None):
            out.append(x.name)
        elif type(x).__name__ == "MatchMapping" and getattr(x, "rest", None):
            out.append(x.rest)
        todo.extend(ast.iter_child_nodes(x))
    return out


def _sets_by_name(tree, name):
    """TREE sets NAME by its string or as an attribute: `globals()[NAME] =
    ...`, `setattr(..., NAME, ...)`, `x.NAME = ...`, a keyword `NAME=`
    (`globals().update(NAME=...)`), a dict key NAME (TJ-3835)."""
    for x in ast.walk(tree):
        if isinstance(x, ast.Subscript) and isinstance(x.ctx, ast.Store) \
                and isinstance(x.slice, ast.Constant) and x.slice.value == name:
            return True
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "setattr" \
                and any(isinstance(a, ast.Constant) and a.value == name for a in x.args):
            return True
        if isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store) and x.attr == name:
            return True
        if isinstance(x, ast.keyword) and x.arg == name:
            return True
        if isinstance(x, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == name
                                           for k in x.keys):
            return True
    return False


def load_tests_files(files):
    """The files among FILES that bind `load_tests` at module scope (any
    binding, TJ-3823; a star import, which may), set it by its name or as
    an attribute, or define a module `__getattr__` (which unittest's
    lookup of it would call): unittest calls it to build a module's or a
    package's suite."""
    out = []
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        scope = _module_scope(tree)
        names = {x for n in scope for x in _scope_names(n)}
        star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
                   for n in scope)
        if names & {"load_tests", "__getattr__"} or star or _sets_by_name(tree, "load_tests"):
            out.append(p)
    return out


def _test_modules():
    return sorted(p for p in _paths.TESTS.rglob("test_*.py")
                  if "__pycache__" not in p.parts)


_HOME_READS = ("expanduser", "expandvars", "Path.home()", 'environ["HOME"]', 'getenv("HOME"',
               'environ.get("HOME"')


def _import_time(nodes):
    """What of NODES (statements at module scope) runs at import: a
    statement whole; of a def, its decorators and defaults; of a class,
    its decorators, bases and keywords, and its body taken the same way."""
    out = []
    for n in nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out += n.decorator_list + [d for d in n.args.defaults + n.args.kw_defaults if d is not None]
        elif isinstance(n, ast.ClassDef):
            out += n.decorator_list + n.bases + [k.value for k in n.keywords] + _import_time(n.body)
        else:
            out.append(n)
    return out


def home_at_import(src):
    """The modules below SRC that read HOME at their import: an
    `expanduser`, `expandvars`, `Path.home()`, `environ["HOME"]`,
    `environ.get("HOME")` or `getenv("HOME")` in what runs at import
    (lib/rate_cache's and lib/crypto_prices' CACHE_FILE). Not seen: a read
    in a function a module-scope statement calls (TJ-3859)."""
    out = set()
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src).with_suffix("").parts
        name = ".".join(rel[:-1] if rel[-1] == "__init__" else rel)
        for n in _import_time(_module_scope(ast.parse(p.read_text(encoding="utf-8")))):
            if any(r in ast.unparse(n).replace("'", '"') for r in _HOME_READS):
                out.add(name)
                break
    return out


def _module_imports(path, package):
    """The modules an import at PATH's module scope runs: each dotted
    prefix, and a from-import's names that are modules (relative ones
    resolved against PACKAGE)."""
    out = []
    for n in _module_scope(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            for a in n.names:
                parts = a.name.split(".")
                out += [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = package.split(".")[:len(package.split(".")) - n.level + 1]
                base = ".".join(up + ([base] if base else []))
            parts = base.split(".") if base else []
            out += [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
            out += [f"{base}.{a.name}" if base else a.name for a in n.names if a.name != "*"]
    return out


def _module_file(name, roots):
    for root in roots:
        base = root.joinpath(*name.split("."))
        for p in (base.with_suffix(".py"), base / "__init__.py"):
            if p.is_file():
                return p
    return None


def reaching_test_modules(trees, srcs):
    """The test modules (with the tree each runs from) whose module-scope
    imports, through the test tree and src, reach a module that reads HOME
    at its import."""
    targets = set().union(*(home_at_import(s) for s in srcs))
    parsed = {}
    out = []
    for tree in trees:
        roots = (tree, *srcs)
        mods = []
        for p in sorted(tree.rglob("test_*.py")):
            rel = p.relative_to(tree).with_suffix("").parts
            if "__pycache__" not in p.parts and not any(
                    x.startswith((".", "_")) or x in ("golden", "fixtures") for x in rel[:-1]):
                mods.append(".".join(rel))
        edges, todo = {}, list(mods)
        while todo:                         # the import graph from the test modules
            name = todo.pop()
            if name in edges:
                continue
            p = _module_file(name, roots)
            if p is None:
                edges[name] = []
                continue
            if p not in parsed:
                parsed[p] = _module_imports(p, name if p.name == "__init__.py" else name.rpartition(".")[0])
            edges[name] = parsed[p]
            todo += parsed[p]
        reach, grew = set(targets), True
        while grew:                         # what reaches a target (cycles too)
            grew = False
            for name, es in edges.items():
                if name not in reach and any(e in reach for e in es):
                    reach.add(name)
                    grew = True
        out += [(tree, m) for m in mods if m in reach]
    return targets, out


# Run alone (`cd tests && python -m unittest test_x`), a module gets the
# synthetic HOME only from its own imports (tests/_hermetic): one that
# loads a module reading HOME at import first binds the HOME it was given,
# a developer's own, and its cache (TJ-3857). A finder first on
# sys.meta_path sees each import start, whatever the module binds
# (TJ-3860).
_PROBE = """
import importlib, os, sys
sys.path[:0] = [os.getcwd()] + ROOTS
given, hits = os.environ["HOME"], []
class Watch:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name in TARGETS and os.environ.get("HOME") == given and name not in hits:
            hits.append(name)
sys.meta_path.insert(0, Watch)
importlib.import_module(MOD)
print(" ".join(hits))
"""


def given_home_bindings(tree, mod, srcs, targets):
    """The modules of TARGETS (each reads HOME at its import) that MOD,
    imported alone from TREE with a HOME of its own, imports while HOME
    is still that one."""
    with tempfile.TemporaryDirectory() as home:
        env = {"HOME": home, "PATH": os.environ.get("PATH", ""), "TAXJSON_OFFLINE": "1",
               "TAXJSON_WIDTH": "0", "LANG": "C.UTF-8"}
        code = (_PROBE.replace("ROOTS", repr([str(s) for s in srcs]))
                .replace("MOD", repr(mod)).replace("TARGETS", repr(sorted(targets))))
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=tree, env=env,
                           capture_output=True, text=True, timeout=600)
    if r.returncode:
        return [f"its import fails: {(r.stderr.strip().splitlines() or ['?'])[-1]}"]
    return r.stdout.split()


class TestPaths(unittest.TestCase):
    def test_the_named_folders(self):
        self.assertTrue((_paths.SRC / "taxjson" / "__init__.py").is_file())
        self.assertEqual(_paths.SRC, _paths.REPO / "src")
        self.assertTrue((_paths.TESTS / "_paths.py").is_file())
        self.assertTrue((_paths.EXAMPLES).is_dir())
        self.assertTrue((_paths.FIXTURES).is_dir())

    def test_no_module_computes_a_folder_from_its_own_file(self):
        bad = []
        for p in _python_files():
            for ln in _own_file_uses(p.read_text(encoding="utf-8")):
                bad.append(f"{p.relative_to(_paths.TESTS)}:{ln}")
        self.assertEqual(bad, [], "name the folder through tests/_paths.py "
                                  "(REPO, SRC, TESTS, EXAMPLES, FIXTURES)")

    def test_the_finder_sees_a_file_name_in_code_only(self):
        text = ('import sys, taxjson\n'
                'A = Path(__file__).parent\n'
                'B = taxjson.__file__\n'
                'C = "Path(__file__).parent"\n'
                'D = f"{Path(__file__).parent}"\n'
                'E = __spec__.origin\n'
                'F = sys.modules[__name__].__file__\n'
                'G = globals()["__file__"]\n'
                'H = getattr(taxjson, "__file__")\n')
        self.assertEqual(_own_file_uses(text), [2, 5, 6, 7, 8])

    def test_the_helpers_are_read_too(self):
        names = {p.name for p in _python_files()}
        self.assertLessEqual({"_golden.py", "_style.py", "dual.py", "test_test_layout.py"}, names)
        # Each exemption is a file there (a moved shim fails here, and its
        # lookup of the shim chain beside it would break too).
        for rel in _OWN_FILE_OK:
            self.assertTrue((_paths.TESTS / rel).is_file(), rel)
        # Only those: another _paths.py or sitecustomize.py is read.
        shims = {p.relative_to(_paths.TESTS).as_posix()
                 for p in _paths.TESTS.rglob("*.py")
                 if p.name in ("_paths.py", "sitecustomize.py") and "__pycache__" not in p.parts}
        self.assertEqual(shims, set(_OWN_FILE_OK))

    def test_each_feature_folder_is_an_area_of_the_map(self):
        """tests/features/<area>/ only for an area FEATURE_AREAS names; the
        areas cover docs/architecture-map.md's sections, each once."""
        feats = _paths.TESTS / "features"
        folders = (sorted(p.name for p in feats.iterdir() if p.is_dir() and p.name != "__pycache__")
                   if feats.is_dir() else [])
        self.assertEqual([f for f in folders if f not in FEATURE_AREAS], [],
                         "a folder below tests/features/ that FEATURE_AREAS does not name")
        loose = (sorted(p.name for p in feats.iterdir() if p.is_file() and p.name != "__init__.py")
                 if feats.is_dir() else [])
        self.assertEqual(loose, [], "a file in tests/features/ itself: a module goes in its area's folder")
        # The mover refuses a folder outside these areas: it reads this table
        # (a moved or annotated table must not switch its check off).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "move_tests_areas", _paths.REPO / "scripts" / "move_tests.py")
        mt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mt)
        self.assertEqual(mt.feature_areas(_paths.REPO), set(FEATURE_AREAS))
        heads = re.findall(r"^## (.+)$", (_paths.REPO / "docs" / "architecture-map.md")
                           .read_text(encoding="utf-8"), re.M)
        named = [h for hs in FEATURE_AREAS.values() for h in hs]
        self.assertEqual(len(named), len(set(named)), "a section in two areas")
        self.assertEqual(sorted(set(named) - set(heads)), [],
                         "not a section of docs/architecture-map.md: rename the area's section")
        self.assertEqual(sorted(set(heads) - set(named)), [],
                         "a section of docs/architecture-map.md no area takes: add it to FEATURE_AREAS")
        for area, sections in FEATURE_AREAS.items():
            self.assertTrue(area.isidentifier() and not area.startswith(("_", "test_")), area)
            self.assertTrue(sections, f"{area}: an area names its section(s)")
        # One folder deep: a module below an area's own folder is refused.
        deep = sorted(p.relative_to(feats).as_posix() for p in feats.glob("*/*")
                      if p.is_dir() and p.name != "__pycache__") if feats.is_dir() else []
        self.assertEqual(deep, [], "a folder below a feature area: move its modules up")
        # No area's folder is one .gitignore drops (`reports/`, the user's
        # report folder, once caught tests/features/reports/).
        if (_paths.REPO / ".git").exists():
            paths = [f"tests/features/{a}/test_x.py" for a in FEATURE_AREAS]
            r = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=_paths.REPO,
                               input="\n".join(paths) + "\n", capture_output=True, text=True)
            self.assertIn(r.returncode, (0, 1), r.stderr)  # 1: none ignored; else git failed
            self.assertEqual(r.stdout.split(), [], "an area's folder .gitignore ignores: "
                             "add a `!/tests/features/<area>/` line after the rule")

    def test_the_round_named_modules_only_fall(self):
        n = sum(1 for p in _test_modules() if _round_named(p.name))
        self.assertLessEqual(n, ROUND_NAMED_MAX,
                             "a new module is named after what it tests, not its round")
        self.assertEqual(n, ROUND_NAMED_MAX,
                         f"{ROUND_NAMED_MAX - n} round-named modules moved: lower "
                         f"ROUND_NAMED_MAX to {n}")

    def test_the_round_name_pattern(self):
        for name in ("test_fix_a2_x.py", "test_fixl_planning.py", "test_fixd3_sl.py", "test_fixn_x.py",
                     "test_audit_tier1_fixes.py", "test_decisions_2026_10_26_cents.py"):
            self.assertTrue(_ROUND_NAMED.match(name), name)
        for name in ("test_fixture_text.py", "test_fixtures.py", "test_engine_x.py"):
            self.assertFalse(_ROUND_NAMED.match(name), name)
        for name in ("test_review_round9_fixes.py", "test_cli_audit_2026_09.py", "test_polish_pass.py",
                     "test_refactor_step1.py", "test_ca_figure_bugs.py", "test_parser_audit_2026_09b.py"):
            self.assertTrue(_round_named(name), name)
        for name in ("test_auditor.py", "test_debugs.py", "test_parser_fixtures.py",
                     "test_slip_audit.py", "test_audit_command.py"):
            self.assertFalse(_round_named(name), name)

    def test_test_modules_share_through_support_only_fall(self):
        files = _all_test_tree_files()
        stems = {p.stem for p in files if p.name.startswith("test_")}
        n = sum(1 for p in files if cross_imports(p, stems))
        self.assertLessEqual(n, CROSS_IMPORTS_MAX,
                             "a helper two test modules share goes in tests/_support/, "
                             "never in a test module another imports")
        self.assertEqual(n, CROSS_IMPORTS_MAX,
                         f"{CROSS_IMPORTS_MAX - n} modules no longer import a test module: "
                         f"lower CROSS_IMPORTS_MAX to {n}")

    def test_what_counts_as_a_cross_import(self):
        stems = {"test_a", "test_b", "test_c", "test_d", "test_e", "test_f", "test_g", "test_h",
                 "test_i", "test_j", "test_k", "test_m", "test_n", "test_o", "test_p", "test_q",
                 "test_r", "test_s", "test_x", "test_y", "test_z"}
        src = ("import json\n"
               "import _paths\n"
               "from test_a import helper\n"
               "def f():\n    import test_b as b\n    return b\n"
               "from rules.ca import test_c\n"
               "import importlib.util\n"
               "path = _paths.TESTS / 'test_d.py'\n"
               "spec = importlib.util.spec_from_file_location('test_d_copy', path)\n"
               "m = importlib.import_module('test_e')\n"
               "NOTE = 'test_x is named here only'\n"
               "from test_self import y\n"
               "f = __import__('test_f')\n"
               "import runpy\n"
               "runpy.run_path('tests/test_g.py')\n"
               "runpy.run_module('test_h')\n"
               "from . import test_i\n"
               "from .test_j import z\n"
               "from unittest import mock\n"
               "with mock.patch('test_k.helper'):\n    pass\n"
               "with mock.patch.dict('test_m.TABLE', {}):\n    pass\n"
               "with mock.patch.object(f, 'test_y'):\n    pass\n"
               "from unittest.mock import patch\n"
               "with patch('test_n.helper'):\n    pass\n"
               "with patch.multiple('test_o', A=1):\n    pass\n"
               "with mock.patch(target='test_p.x'):\n    pass\n"
               "client = f\n"
               "client.patch('api.test_z.x')\n"
               "from importlib.machinery import SourceFileLoader\n"
               "SourceFileLoader('x', 'tests/test_q.py')\n"
               "import unittest.mock\n"
               "unittest.mock.patch('test_r.y')\n"
               "mock.patch.dict(in_dict='test_s.TABLE', values={})\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "test_self.py"
            p.write_text(src, encoding="utf-8")
            self.assertEqual(sorted(cross_imports(p, stems | {"test_self"})),
                             ["test_a", "test_b", "test_c", "test_d", "test_e", "test_f",
                              "test_g", "test_h", "test_i", "test_j", "test_k", "test_m",
                              "test_n", "test_o", "test_p", "test_q", "test_r", "test_s"])

    def test_no_test_class_runs_twice(self):
        # A TestCase class bound in a second module runs there too (10
        # tests ran twice before step 9 B1).
        twice = bound_test_classes(_all_test_tree_files(), _test_trees())
        self.assertEqual(twice, [], "import the module, not its TestCase class, or move "
                                    "what the class shares to tests/_support/")

    def test_no_helper_holds_a_test_case(self):
        # Two modules importing a helper's TestCase would both run it, under
        # one id (TJ-3759): a helper holds mixins, a test module the class.
        held = held_test_cases(_all_test_tree_files(), _paths.REPO, _test_trees())
        self.assertEqual(held, [], "a helper holds a mixin; the TestCase that runs it is "
                                   "in a test module")

    def test_no_class_breaks_its_support_mixin(self):
        # TJ-3947: a class built on a tests/_support mixin lists the mixin
        # first; a class-level marker or skip goes on the mixin.
        found = support_mixin_problems(_all_test_tree_files(), _test_trees())
        self.assertEqual(found, [], "list the mixin before unittest.TestCase; put the "
                                    "class's @rule, @rule_absent and skips on the mixin")

    def test_a_support_mixin_of_tests_is_named_cases(self):
        # TJ-3988: a tests/_support class whose tests a TestCase built on
        # it runs is `<Subject>Cases`, so a reader sees it holds tests.
        found = misnamed_support_mixins(_all_test_tree_files(), _test_trees())
        self.assertEqual(found, [], "name a tests/_support mixin of tests `<Subject>Cases`")

    def test_what_a_support_mixin_of_tests_is(self):
        # TJ-3988: a class of tests/_support (or a tree's single-file
        # _support.py) with tests of its own or from a base it is built
        # on, no TestCase, is named `...Cases`; a helper or fixture without
        # tests, a TestCase (held_test_cases' to refuse) and a class outside
        # tests/_support are not judged.
        files = {
            "tests/_support/__init__.py": "",
            "tests/_support/shared.py":
                "import unittest\n"
                "from _support.more import DeepCases\n"
                "class FooCases:\n    def test_a(self): pass\n"
                "class Shared:\n    def setUp(self): pass\n    def test_b(self): pass\n"
                "class _Via(FooCases):\n    pass\n"
                "class Deep(DeepCases):\n    def helper(self): pass\n"
                "class Helper:\n    def helper(self): pass\n    def check(self): pass\n"
                "class _Fix(unittest.TestCase):\n    def setUp(self): pass\n"
                "class Held(unittest.TestCase):\n    def test_c(self): pass\n"
                "class _OnFix(_Fix):\n    def test_d(self): pass\n"
                "class _Layout:\n    run = None\n"
                "class ViaCases(_Layout, DeepCases):\n    pass\n",
            "tests/_support/more.py": "class DeepCases:\n    def test_deep(self): pass\n"
                                      "class CasesBase:\n    def test_i(self): pass\n"
                                      "class Foocases:\n    def test_j(self): pass\n",
            "tests/helper.py": "class NotSupport:\n    def test_e(self): pass\n",
            "tests/test_x.py": "class Plain:\n    def test_f(self): pass\n",
            "packages/p/tests/_support.py":
                "class One:\n    def test_g(self): pass\n"
                "class TwoCases:\n    def test_h(self): pass\n",
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            trees = [root / "tests", root / "packages/p/tests"]
            self.assertEqual(misnamed_support_mixins(sorted(root.rglob("*.py")), trees), [
                "packages/p/tests/_support.py:1: One",
                "tests/_support/more.py:3: CasesBase",
                "tests/_support/more.py:5: Foocases",
                "tests/_support/shared.py:5: Shared",
                "tests/_support/shared.py:8: _Via",
                "tests/_support/shared.py:10: Deep"])

    def test_each_support_module_is_named_for_its_subject(self):
        # TJ-3999: a broker, country or area prefix on every module.
        self.assertEqual(unprefixed_support(_paths.TESTS / "_support"), [],
                         "start the module's name with one of SUPPORT_PREFIXES (a new "
                         "prefix goes there, in its group)")

    def test_what_a_support_prefix_is(self):
        # The name starts with a whole prefix, underscore too; a module is
        # a .py file or a folder; `__init__.py`, `__pycache__` and data
        # files are not judged.
        self.assertTrue(all(p.endswith("_") for p in SUPPORT_PREFIXES))
        self.assertEqual(len(set(SUPPORT_PREFIXES)), len(SUPPORT_PREFIXES))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel in ("__init__.py", "ib_rows.py", "ca_slips.py", "cash_rows.py",
                        "old_ib_rows.py", "notes.txt", "__pycache__/x.cpython-312.pyc",
                        "planning_tools/__init__.py", "pkg/__init__.py",
                        # a bare prefix word, a folder without __init__.py,
                        # the prefix in another case
                        "ib.py", "data/rows.csv", "IB_rows.py"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("", encoding="utf-8")
            self.assertEqual(unprefixed_support(root),
                             ["IB_rows", "cash_rows", "data", "ib", "old_ib_rows", "pkg"])

    def test_the_tests_a_child_reruns_are_discovered(self):
        # TJ-3949: test_temp_isolation re-runs these ids in a child process
        # by name; a renamed or moved test fails here, not as a child's
        # import error.
        ids = child_run_ids(_paths.TESTS / "test_temp_isolation.py")
        self.assertTrue(ids)
        self.assertEqual([why for i in ids if (why := undiscovered(i, _paths.TESTS))], [],
                         "rename the id in tests/test_temp_isolation.py `_AFFECTED`")

    def test_no_module_builds_its_own_suite(self):
        # A load_tests decides what a module runs, past the runner's
        # by-class check of what is bound where (TJ-3811, TJ-3812): none
        # has one; a module parameterises with subTest or a class a case.
        found = load_tests_files(_all_test_tree_files())
        self.assertEqual([p.relative_to(_paths.REPO).as_posix() for p in found], [],
                         "build the cases with subTest, or a class per case, not load_tests")

    def test_what_binds_load_tests(self):
        cases = {
            "a def": ("def load_tests(loader, tests, pattern):\n    return tests\n", True),
            "an assignment": ("load_tests = None\n", True),
            "an import": ("from _helper import load_tests\n", True),
            "under an if": ("import os\nif os.sep:\n    def load_tests(*a):\n        pass\n", True),
            "a def's own": ("def f():\n    def load_tests(*a):\n        pass\n", False),
            "a class's": ("class C:\n    load_tests = None\n", False),
            "another name": ("def load_test(*a):\n    pass\n", False),
            # TJ-3825: each spelling of a binding.
            "an import's asname": ("from _helper import f as load_tests\n", True),
            "a dotted import": ("import load_tests.sub\n", True),
            "an unpacking": ("load_tests, X = None, 1\n", True),
            "an annotation": ("load_tests: object = None\n", True),
            "an async def": ("async def load_tests(*a):\n    pass\n", True),
            "an import of another name": ("from _helper import load_tests as f\n", False),
            "an attribute read": ("import x\nF = x.load_tests\n", False),
            # TJ-3823
            "a class": ("class load_tests:\n    pass\n", True),
            "a for target": ("for load_tests in [None]:\n    pass\n", True),
            "a with target": ("import contextlib\nwith contextlib.nullcontext() as load_tests:\n"
                              "    pass\n", True),
            "a walrus": ("X = (load_tests := None)\n", True),
            "an except target": ("try:\n    pass\nexcept OSError as load_tests:\n    pass\n", True),
            "a match capture": ("match 1:\n    case load_tests:\n        pass\n", True),
            "globals by name": ("globals()['load_tests'] = None\n", True),
            "setattr by name": ("import sys\nsetattr(sys.modules[__name__], 'load_tests', None)\n",
                                True),
            "a module __getattr__": ("def __getattr__(name):\n    raise AttributeError(name)\n", True),
            "a lambda's own": ("F = lambda load_tests: load_tests\n", False),
            # TJ-3835
            "an attribute store": ("import sys\nsys.modules[__name__].load_tests = None\n", True),
            "a star import": ("from _helper import *\n", True),
            "globals().update": ("globals().update(load_tests=None)\n", True),
            "a dict key": ("globals().update({'load_tests': None})\n", True),
            "a star import in a def": ("def f():\n    exec('from _helper import *')\n", False),
            # TJ-3838: the match rest, keyword-only defaults, a class keyword.
            "a match rest": ("match {}:\n    case {**load_tests}:\n        pass\n", True),
            "a keyword-only default": ("def g(*, x=(load_tests := 1)):\n    pass\n", True),
            "a class keyword": ("class C(metaclass=(load_tests := type)):\n    pass\n", True),
            "a lambda's walrus": ("F = lambda: (load_tests := 1)\n", False),
            "a decorator's walrus": ("@(load_tests := (lambda f: f))\ndef g():\n    pass\n", True),
            "a def's default walrus": ("def g(x=(load_tests := 1)):\n    pass\n", True),
            "a class's __getattr__": ("class C:\n    def __getattr__(self, n):\n        pass\n", False),
            "the name read": ("X = load_tests_files\n", False),
        }
        with tempfile.TemporaryDirectory() as td:
            for why, (text, hit) in cases.items():
                with self.subTest(why):
                    p = Path(td) / "test_x.py"
                    p.write_text(text)
                    self.assertEqual(load_tests_files([p]), [p] if hit else [])

    def test_the_trees_are_every_package_s_too(self):
        # TJ-3825: the rules read each package's test tree, not tests/ only.
        trees = _test_trees()
        self.assertEqual(trees[0], _paths.TESTS)
        self.assertIn(_paths.REPO / "packages" / "taxjson-fetch" / "tests", trees)
        files = _all_test_tree_files()
        self.assertTrue(any(_paths.REPO / "packages" in p.parents for p in files))
        self.assertFalse(any("__pycache__" in p.parts for p in files))

    def test_what_binds_a_test_class_twice(self):
        files = {
            "tests/test_def.py": "import unittest\n"
                                 "class TestFoo(unittest.TestCase):\n    def test_a(self): pass\n"
                                 "class Base(unittest.TestCase):\n    pass\n"
                                 "class TestBar(Base):\n    def test_b(self): pass\n",
            "tests/test_use1.py": "from test_def import TestFoo\n",
            "tests/test_use2.py": "import test_def as d\nBorrowed = d.TestFoo\n",
            "tests/test_use3.py": "from test_def import *\n",
            "tests/test_use4.py": "try:\n    from test_def import TestBar\n"
                                  "except ImportError:\n    pass\n",
            "tests/pkg/__init__.py": "",
            "tests/pkg/test_use5.py": "from ..test_def import TestFoo as T\n",
            "tests/test_fine.py": "import test_def\nfrom test_def import Base\n"
                                  "X = test_def.TestFoo()\n"
                                  "CASES = [test_def.TestFoo]\n"
                                  "PAIR = (test_def.TestFoo, test_def.TestBar)\n"
                                  "def f():\n    from test_def import TestFoo\n"
                                  "class Holder:\n    from test_def import TestFoo\n"
                                  "from typing import TYPE_CHECKING\n"
                                  "if TYPE_CHECKING:\n    from test_def import TestBar\n"
                                  "if __name__ == '__main__':\n"
                                  "    from test_def import TestFoo\n",
            "tests/test_use6.py": "try:\n    pass\nexcept Exception:\n"
                                  "    from test_def import TestFoo\n",
            "tests/test_use7.py": "import test_def as d\nA, B = d.TestFoo, d.TestBar\n"
                                  "C: type = d.TestFoo\n",
            "tests/test_use8.py": "from pkg import test_def\nX = test_def.TestFoo\n",
            "tests/test_core_only.py": "import unittest\n"
                                       "class TestC(unittest.TestCase):\n"
                                       "    def test_c(self): pass\n",
            "tests/_helper.py": "from test_def import TestFoo\n",
            "tests/_support/shared.py": "import unittest\n"
                                        "class TestShared(unittest.TestCase):\n"
                                        "    def test_s(self): pass\n"
                                        "class Mixin:\n    def test_m(self): pass\n",
            "packages/p/tests/test_def.py": "import unittest\n"
                                            "class TestPlug(unittest.TestCase):\n"
                                            "    def test_p(self): pass\n",
            "packages/p/tests/test_puse.py": "from test_def import TestPlug, TestFoo\n"
                                             "from test_core_only import TestC\n",
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            trees = [root / "tests", root / "packages/p/tests"]
            got = bound_test_classes(sorted(root.rglob("*.py")), trees)
            self.assertEqual(got, [
                "packages/p/tests/test_puse.py:1: test_def.TestPlug",
                "packages/p/tests/test_puse.py:2: test_core_only.TestC",
                "tests/_helper.py:1: test_def.TestFoo",
                "tests/pkg/test_use5.py:1: test_def.TestFoo",
                "tests/test_use1.py:1: test_def.TestFoo",
                "tests/test_use2.py:2: test_def.TestFoo",
                "tests/test_use3.py:1: test_def.TestBar",
                "tests/test_use3.py:1: test_def.TestFoo",
                "tests/test_use4.py:2: test_def.TestBar",
                "tests/test_use6.py:4: test_def.TestFoo",
                "tests/test_use7.py:2: test_def.TestFoo",
                "tests/test_use7.py:2: test_def.TestBar",
                "tests/test_use7.py:3: test_def.TestFoo",
                "tests/test_use8.py:2: test_def.TestFoo"])
            self.assertEqual(test_case_classes(ast.parse(files["tests/test_def.py"])),
                             {"TestFoo", "TestBar"})
            # A helper may hold a mixin, never a TestCase with tests (TJ-3759).
            self.assertEqual(held_test_cases(sorted(root.rglob("*.py")), root),
                             ["tests/_support/shared.py: TestShared"])

    def test_what_a_support_mixin_gives_and_breaks(self):
        # TJ-3947: the tests of a class built on a tests/_support mixin
        # (one there, one it is built on, one under another name, a support
        # fixture base that is a TestCase), and what a layout check refuses.
        files = {
            "tests/_support/__init__.py": "",
            "tests/_support/shared.py":
                "import unittest\n"
                "from _support.more import DeepCases\n"
                "class FooCases:\n    def setUp(self): self.x = 1\n"
                "    def test_a(self): pass\n"
                "class HelperMixin:\n    def helper(self): pass\n"
                "class MoreCases(FooCases):\n    def test_b(self): pass\n"
                "class ViaCases(DeepCases):\n    pass\n"
                "class _Fixture(unittest.TestCase):\n    def setUp(self): pass\n"
                "class HeldCase(FooCases, unittest.TestCase):\n    pass\n",
            "tests/_support/more.py": "class DeepCases:\n    def test_deep(self): pass\n",
            "tests/test_good.py":
                "import unittest\n"
                "from tax_rules import rule\n"
                "from taxjson.rules import ids as R\n"
                "from _support.shared import FooCases, HelperMixin, ViaCases, _Fixture\n"
                "from _support.shared import MoreCases as M\n"
                "class TestFoo(FooCases, unittest.TestCase):\n    pass\n"
                "@rule(R.CA_SL_02)\n"
                "class TestOwn(HelperMixin, unittest.TestCase):\n    def test_c(self): pass\n"
                "class TestM(M, unittest.TestCase):\n    pass\n"
                "class TestVia(ViaCases, unittest.TestCase):\n    pass\n"
                "class TestFix(_Fixture):\n    def test_d(self): pass\n"
                "class TestHelperOnly(HelperMixin, unittest.TestCase):\n    pass\n"
                "class NotACase(FooCases):\n    pass\n",
            "tests/test_bad.py":
                "import unittest\n"
                "from tax_rules import rule, rule_absent\n"
                "from taxjson.rules import ids as R\n"
                "from _support.shared import FooCases, HelperMixin, MoreCases\n"
                "class TestLate(unittest.TestCase, FooCases):\n    pass\n"
                "class TestLateHelper(unittest.TestCase, HelperMixin):\n"
                "    def test_e(self): pass\n"
                "@rule(R.CA_SL_02)\n"
                "class TestRuled(FooCases, unittest.TestCase):\n    pass\n"
                "@unittest.skipIf(True, 'x')\n"
                "class TestSkipped(MoreCases, unittest.TestCase):\n    pass\n"
                "@rule_absent(R.CA_SL_02, country='usa')\n"
                "class TestAbsent(FooCases, unittest.TestCase):\n    def test_f(self): pass\n"
                "@unittest.skip('x')\n"
                "class TestOwnSkip(HelperMixin, unittest.TestCase):\n"
                "    def test_g(self): pass\n",
            "tests/test_binds.py": "from test_good import TestFoo, TestOwn, NotACase\n",
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            tests = root / "tests"
            good = ast.parse(files["tests/test_good.py"])
            self.assertEqual(test_case_classes(good, tests),
                             {"TestFoo", "TestOwn", "TestM", "TestVia", "TestFix"})
            # Without the tree (as before): its own tests only.
            self.assertEqual(test_case_classes(good), {"TestOwn"})
            files_ = sorted(root.rglob("*.py"))
            self.assertEqual(bound_test_classes(files_, [tests]),
                             ["tests/test_binds.py:1: test_good.TestFoo",
                              "tests/test_binds.py:1: test_good.TestOwn"])
            self.assertEqual(held_test_cases(files_, root, [tests]),
                             ["tests/_support/shared.py: HeldCase"])
            self.assertEqual(support_mixin_problems(files_, [tests]), [
                "tests/test_bad.py:5: TestLate: unittest.TestCase before the support mixin "
                "FooCases",
                "tests/test_bad.py:7: TestLateHelper: unittest.TestCase before the support "
                "mixin HelperMixin",
                "tests/test_bad.py:9: TestRuled: a class-level @rule on a class whose tests "
                "come from a support mixin",
                "tests/test_bad.py:12: TestSkipped: a class-level @skipIf on a class whose "
                "tests come from a support mixin",
                "tests/test_bad.py:15: TestAbsent: a class-level @rule_absent on a class whose "
                "tests come from a support mixin"])

    def test_what_discovery_finds_by_id(self):
        # TJ-3949: an id is found in the source tree, as discovery finds it.
        files = {
            "tests/_support/__init__.py": "",
            "tests/_support/m.py": "class XCases:\n    def test_mixed(self): pass\n",
            "tests/test_a.py":
                "import unittest\n"
                "from _support.m import XCases\n"
                "class Base(unittest.TestCase):\n    def test_inherited(self): pass\n"
                "class TestA(Base):\n    def test_own(self): pass\n"
                "    def helper(self): pass\n"
                "class TestX(XCases, unittest.TestCase):\n    pass\n"
                "class Plain:\n    def test_p(self): pass\n",
            "tests/pkg/__init__.py": "",
            "tests/pkg/test_b.py":
                "import unittest\nclass TestB(unittest.TestCase):\n    def test_b(self): pass\n",
            "tests/nopkg/test_c.py":
                "import unittest\nclass TestC(unittest.TestCase):\n    def test_c(self): pass\n",
            "tests/helper.py":
                "import unittest\nclass TestH(unittest.TestCase):\n    def test_h(self): pass\n",
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            tests = root / "tests"
            for i in ("test_a.TestA.test_own", "test_a.TestA.test_inherited",
                      "test_a.TestX.test_mixed", "pkg.test_b.TestB.test_b"):
                self.assertIsNone(undiscovered(i, tests), i)
            for i, why in (("test_a.TestA.test_gone", "no test method test_gone"),
                           ("test_a.TestA.helper", "no test method helper"),
                           ("test_a.TestGone.test_own", "no TestCase class TestGone with tests"),
                           ("test_a.Plain.test_p", "no TestCase class Plain with tests"),
                           ("test_gone.TestA.test_own", "no test_gone.py"),
                           ("nopkg.test_c.TestC.test_c", "nopkg/ is not a package"),
                           ("helper.TestH.test_h", "helper.py is not a test module"),
                           ("test_a", "not module.Class.method")):
                self.assertEqual(undiscovered(i, tests), f"{i}: {why}")
            p = root / "test_iso.py"
            p.write_text("X = 1\n_AFFECTED = (\n    'test_a.TestA.test_own',\n"
                         "    'pkg.test_b.TestB.test_b',\n)\n", encoding="utf-8")
            self.assertEqual(child_run_ids(p), ["test_a.TestA.test_own", "pkg.test_b.TestB.test_b"])

    def test_every_folder_of_test_modules_is_a_package(self):
        missing = set()
        for p in _test_modules():
            d = p.parent
            while d != _paths.TESTS:
                if not (d / "__init__.py").is_file():
                    missing.add(str(d.relative_to(_paths.TESTS)))
                d = d.parent
        self.assertEqual(sorted(missing), [],
                         "add an __init__.py: unittest skips the folder's tests silently")


# A compound statement's header: what of it runs before (or instead of)
# its statements (an except's types and a match's guards: `_at_import`).
_HEADERS = {ast.If: ("test",), ast.While: ("test",), ast.For: ("target", "iter"),
            ast.AsyncFor: ("target", "iter"), ast.With: ("items",), ast.AsyncWith: ("items",),
            ast.Try: (), ast.TryStar: (), ast.Match: ("subject",)}
# The methods of `os.environ` that change it; `get` reads it; any other use
# (`copy`, `items`, an argument, an operand, `**`, a loop over it) copies it.
_ENVIRON_WRITES = ("setdefault", "update", "pop", "popitem", "clear", "__setitem__", "__delitem__",
                   "__ior__")


def _at_import(body):
    """(node, parent) for what of BODY (a module's or a class's statements)
    runs at import: a simple statement whole; a compound statement's
    header alone (an if's or while's test, a loop's target and iterable, a
    with's items, an except's types, a match's subject and guards), its
    statements taken the same way; a def's decorators and defaults; a
    class's decorators, bases and keywords, and its body. Not a block no
    test run imports (`_never_imported`)."""
    for n in body:
        if _never_imported(n):
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            for e in [*n.decorator_list, *a.defaults, *(d for d in a.kw_defaults if d is not None)]:
                yield e, n
        elif isinstance(n, ast.ClassDef):
            for e in [*n.decorator_list, *n.bases, *(k.value for k in n.keywords)]:
                yield e, n
            yield from _at_import(n.body)
        elif type(n) in _HEADERS:
            for field in _HEADERS[type(n)]:
                v = getattr(n, field)
                for e in v if isinstance(v, list) else [v]:
                    yield e, n
            for h in getattr(n, "handlers", ()):
                if h.type is not None:
                    yield h.type, h
                yield from _at_import(h.body)
            for c in getattr(n, "cases", ()):
                if c.guard is not None:
                    yield c.guard, c
                yield from _at_import(c.body)
            for field in ("body", "orelse", "finalbody"):
                yield from _at_import(getattr(n, field, []))
        else:
            yield n, None


def _below(node, parent, parents):
    """NODE and every node below it, PARENTS recording each one's parent;
    not a lambda's body (it runs when the lambda is called)."""
    todo = [(node, parent)]
    while todo:
        n, p = todo.pop()
        parents[n] = p
        yield n
        todo += [(c, n) for c in ast.iter_child_nodes(n) if not (isinstance(n, ast.Lambda) and c is n.body)]


def environ_at_import(files):
    """The (file, line, what) of each use of `os.environ` (or
    `os.environb`) at a module's import in FILES, but a read (`.get`,
    `environ[key]`, `key in environ`), that leaks across tests (TJ-3951,
    TJ-3952, TJ-3958): "copies", any other use (`dict(os.environ, ...)`,
    `.copy()`, `.items()`, `copy.copy`, `{**os.environ}`, `os.environ |
    {...}`, a comprehension or a loop over it: a test that sets a
    variable and is the first to import the module would leave it in
    every later run; build it with `_hermetic.environ(...)`, the
    environment the setup left); "writes", a store, `del`, `|=`,
    `setdefault`, `update`, `pop`, `popitem` or `clear`, `os.putenv` /
    `os.unsetenv`, or a `patch.dict` of it started there (`.start()`, on
    the patch or a name it is bound to) (every later test in the process
    would see it: set it in `setUpModule` and restore it). Through the
    names the module binds at its import to `os` (`import os as o`), to
    `os.environ` (`from os import environ as e`, `E = os.environ`), to
    `os.putenv` / `os.unsetenv`, and to such a patch. A `patch.dict` of it
    as a decorator or in a `with`, or one that is only made, is no use.
    What runs at import: `_at_import` (no `__main__` or TYPE_CHECKING
    block, no def's or lambda's body). Not seen: a function the import
    calls (`tests/_tmpfiles.py`'s `isolate_layouts()`: TJ-3961;
    packages/taxjson-fetch/tests/_bootstrap.py's `_setup()`, which puts
    the plugin on PYTHONPATH: TJ-3973)."""
    out = []
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        roots = list(_at_import(tree.body))
        mods, envs, calls, patches = {"os"}, set(), set(), set()

        def is_env(n):
            return (isinstance(n, ast.Name) and n.id in envs) or (
                isinstance(n, ast.Attribute) and n.attr in ("environ", "environb")
                and isinstance(n.value, ast.Name) and n.value.id in mods)

        def env_patch(n):           # `patch.dict(os.environ, ...)`, or of "os.environ"
            return isinstance(n, ast.Call) and _patch_kind(n.func) == "dict" and (
                _patch_target(n) == "os.environ"
                or any(is_env(a) for a in [*n.args[:1], *(k.value for k in n.keywords if k.arg == "in_dict")]))

        for n, _p in roots:         # the names bound at import, aliases of aliases too
            if isinstance(n, ast.Import):
                mods.update(a.asname or "os" for a in n.names
                            if a.name == "os" or (a.name.startswith("os.") and not a.asname))
            elif isinstance(n, ast.ImportFrom) and n.module == "os" and not n.level:
                for a in n.names:
                    if a.name in ("environ", "environb", "*"):
                        envs.update(("environ", "environb") if a.name == "*" else (a.asname or a.name,))
                    if a.name in ("putenv", "unsetenv", "*"):
                        calls.update(("putenv", "unsetenv") if a.name == "*" else (a.asname or a.name,))
        grew = True
        while grew:
            before = len(envs) + len(patches)
            for n, _p in roots:
                if isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets):
                    names = {t.id for t in n.targets}
                    if is_env(n.value):
                        envs |= names
                    elif env_patch(n.value):
                        patches |= names
            grew = len(envs) + len(patches) > before
        parents, hits = {}, []
        for root, parent in roots:
            for n in _below(root, parent, parents):
                p = parents[n]
                what = None
                if isinstance(n, ast.Call):
                    fn = n.func
                    if (isinstance(fn, ast.Attribute) and fn.attr in ("putenv", "unsetenv")
                            and isinstance(fn.value, ast.Name) and fn.value.id in mods) \
                            or (isinstance(fn, ast.Name) and fn.id in calls):
                        what = "writes"
                    elif isinstance(fn, ast.Attribute) and fn.attr in ("start", "__enter__") and (
                            env_patch(fn.value) or (isinstance(fn.value, ast.Name) and fn.value.id in patches)):
                        what = "writes"
                elif is_env(n):
                    gp = parents.get(p)
                    if env_patch(p) or (isinstance(p, ast.keyword) and env_patch(gp)):
                        pass                                    # judged where it is started
                    elif isinstance(p, ast.Attribute):          # os.environ.<name>
                        what = (None if p.attr == "get" else
                                "writes" if p.attr in _ENVIRON_WRITES else "copies")
                    elif isinstance(p, ast.Subscript) and p.value is n:
                        what = None if isinstance(p.ctx, ast.Load) else "writes"
                    elif isinstance(p, ast.Compare) and any(
                            c is n and isinstance(op, (ast.In, ast.NotIn)) for op, c in zip(p.ops, p.comparators)):
                        pass                                    # `key in os.environ`
                    elif isinstance(p, ast.AugAssign) and p.target is n:
                        what = "writes"
                    elif isinstance(p, (ast.Assign, ast.Delete)) and n in getattr(p, "targets", ()):
                        what = None if isinstance(n, ast.Name) else "writes"   # a name rebound, or os.environ
                    elif isinstance(p, ast.Assign) and p.value is n and all(
                            isinstance(t, ast.Name) for t in p.targets):
                        pass                                    # an alias (`E = os.environ`)
                    else:
                        what = "copies"
                if what:
                    hits.append((n.lineno, what))
        out += [(f, line, what) for line, what in sorted(hits)]
    return out


# The setup's record of the environment it leaves: the one copy allowed.
_ENVIRON_RECORD = "ENVIRON = dict(os.environ)"


def environ_leaks(found, setup):
    """(line, rest): the line of SETUP's record of the environment it
    leaves (`_ENVIRON_RECORD`, a statement at its module scope; None
    unless it is there once), and FOUND (`environ_at_import`'s hits)
    but that statement's, the one copy allowed: any other hit in SETUP
    stays (TJ-3962, TJ-3974)."""
    lines = [n.lineno for n in ast.parse(setup.read_text(encoding="utf-8")).body
             if ast.unparse(n).replace("'", '"') == _ENVIRON_RECORD]
    line = lines[0] if len(lines) == 1 else None
    return line, [h for h in found if (h[0], h[1]) != (setup, line)]


# A child that runs test_kraken_template's tests through unittest (its
# setUpModule and the module cleanups, as a run calls them), with the zone
# unset and then set: the zone its tests had and the one left after them
# (TJ-3952). The module by its name, as a run names it: scripts/move_tests.py
# does not rewrite it but lists it among the mentions for the batch to fix
# by hand, and a name left stale fails the child loudly (ModuleNotFoundError).
_KRAKEN = "test_kraken_template"
_ZONE_AROUND = """
import importlib, io, json, os, sys, unittest
sys.path[:0] = ROOTS
import _hermetic  # first, as a run imports it
mod = importlib.import_module(MOD)
zone, seen, set_up = "TAXJSON_LOCAL_TZ", [], mod.setUpModule
def spy():
    set_up()
    seen.append(os.environ.get(zone))
mod.setUpModule = spy
out = []
for before in (None, "Asia/Tokyo"):
    os.environ.pop(zone, None)
    if before:
        os.environ[zone] = before
    r = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(mod))
    out.append([before, seen.pop(), os.environ.get(zone), r.testsRun, len(r.failures) + len(r.errors)])
print(json.dumps(out))
"""


class TestRunAlone(unittest.TestCase):
    def test_a_module_run_alone_binds_no_given_home(self):
        # TJ-3857: the hermetic setup before a module that reads HOME at
        # its import, for every test module whose imports reach one.
        srcs = (_paths.SRC, *sorted((_paths.REPO / "packages").glob("*/src")))
        targets, mods = reaching_test_modules(_test_trees(), srcs)
        self.assertTrue({"taxjson.lib.rate_cache", "taxjson.lib.crypto_prices"} <= targets, targets)
        self.assertIn("rules.ca.fx.test_foreign_cash_ledger_v2", [m for _t, m in mods])
        with ThreadPoolExecutor(4) as ex:
            found = list(ex.map(lambda tm: (tm[1], given_home_bindings(tm[0], tm[1], srcs, targets)), mods))
        bad = [f"{m}: {', '.join(xs)}" for m, xs in found if xs]
        self.assertEqual(bad, [], "run alone, these import a module that reads HOME at import "
                                  "before the hermetic setup (import _hermetic before the import "
                                  "that loads it):\n" + "\n".join(bad))

    def test_no_module_changes_or_copies_the_environment_at_import(self):
        # TJ-3951, TJ-3952: what a module does to os.environ, or takes
        # from it, at its import leaks across the tests of its process;
        # the core's tests and the plugin's, its bootstrap too (TJ-3957,
        # TJ-3973).
        setup, files = _paths.TESTS / "_hermetic" / "__init__.py", _all_test_tree_files()
        self.assertTrue(any(_paths.REPO / "packages" in f.parents for f in files), "the plugin's tests are scanned")
        found = environ_at_import(files)
        record, rest = environ_leaks(found, setup)
        self.assertIsNotNone(record, f"tests/_hermetic records its environment once: {_ENVIRON_RECORD}")
        self.assertIn((setup, record, "copies"), found)         # seen, and the one allowed
        bad = [f"{f.relative_to(_paths.REPO)}:{line}: {what} os.environ" for f, line, what in rest]
        self.assertEqual(bad, [], "at their import (build an environment with _hermetic.environ(...); "
                                  "set a variable in setUpModule and restore it):\n" + "\n".join(bad))

    def test_what_changes_or_copies_the_environment_at_import(self):
        with tempfile.TemporaryDirectory() as td:
            cases = {"a.py": "import os\n\nENV = dict(os.environ, X='1')\n",
                     "b.py": "import os\n\nENV = os.environ.copy()\n",
                     "c.py": "import os\n\nENV = {**os.environ, 'X': '1'}\n",
                     "d.py": "import os\n\n\nclass C:\n    ENV = dict(os.environ)\n",
                     "e.py": "import os\n\n\ndef f(env=os.environ.copy()):\n    return env\n",
                     "f.py": "import os\n\n\ndef f():\n    os.environ['X'] = '1'\n    return dict(os.environ)\n",
                     "g.py": "import os\n\nENV = dict(os.environ.items())\n",
                     "h.py": "import _hermetic\n\nENV = _hermetic.environ(X='1')\n",
                     "i.py": "import os\n\nS = 'dict(os.environ)'\nH = os.environ.get('X')\n",
                     "j.py": "import os\n\nos.environ.setdefault('X', '1')\n",
                     "k.py": "import os\n\nos.environ['X'] = '1'\n",
                     "l.py": "import os\n\nif True:\n    del os.environ['X']\n",
                     "m.py": "import os\n\nos.environ.update(X='1')\nos.putenv('Y', '1')\n",
                     "n.py": "import os\n\n\ndef setUpModule():\n    os.environ['X'] = '1'\n",
                     # TJ-3958: every use but a read, through aliases too.
                     "o.py": "import copy\nimport os\n\nA = os.environ | {'X': '1'}\nB = copy.copy(os.environ)\n"
                             "C = dict(**os.environ, X='1')\nD = {k: v for k, v in os.environ.items() if k}\n"
                             "for _k in os.environ:\n    pass\n",
                     "p.py": "from os import environ as e, unsetenv as u\n\nENV = dict(e)\ne['X'] = '1'\nu('Y')\n"
                             "Z = e.get('X')\n",
                     "q.py": "import os as _os\n\nENV = _os.environ.copy()\n_os.environ |= {'X': '1'}\n"
                             "_os.putenv('Y', '1')\n",
                     "r.py": "import os\n\nenv = os.environ\nenv['X'] = '1'\nY = env.get('Y')\n",
                     "s.py": "import os\nfrom unittest import mock\n\nmock.patch.dict(os.environ, X='1').start()\n"
                             "ZONE = mock.patch.dict('os.environ', {'Y': '1'})\nZONE.start()\n",
                     "t.py": "import os\nimport unittest\nfrom unittest import mock\n\n"
                             "with mock.patch.dict(os.environ, {'X': '1'}):\n    import json\n\n\n"
                             "@mock.patch.dict(os.environ, {'X': '1'})\nclass T(unittest.TestCase):\n    pass\n\n\n"
                             "P = mock.patch.dict(in_dict=os.environ, values={'X': '1'})\n\n\n"
                             "def setUpModule():\n    P.start()\n    unittest.addModuleCleanup(P.stop)\n",
                     "u.py": "import os\n\nif 'X' in os.environ and os.environ['X'] and 'Y' not in os.environ:\n"
                             "    pass\n",
                     # TJ-3962: the other writes; another object's `environ`.
                     "v.py": "import os\n\nos.environ.pop('X', None)\nos.environ.clear()\nos.unsetenv('Y')\n",
                     "w.py": "class Env:\n    environ = {}\n\n\nE = Env()\nE.environ.update(X='1')\n",
                     # TJ-3959: what does not run at import; a block's own statements do.
                     "x.py": "import os\nimport unittest\nfrom typing import TYPE_CHECKING\n\n"
                             "if TYPE_CHECKING:\n    E = dict(os.environ)\n"
                             "if __name__ == '__main__':\n    os.environ['X'] = '1'\n    unittest.main()\n",
                     "y.py": "import os\nimport sys\n\nif sys.platform:\n    def f():\n        os.environ['X'] = '1'\n"
                             "try:\n    import nothing_here\nexcept ImportError:\n    class C:\n"
                             "        def setUp(self):\n            self.env = dict(os.environ)\n"
                             "with open(os.devnull):\n    def g():\n        return os.environ.copy()\n"
                             "    H = os.environ.copy()\n",
                     "z.py": "import os\n\nF = lambda **kw: dict(os.environ, **kw)\n"
                             "G = lambda env=os.environ.copy(): env\n",
                     "sub/aa.py": "import os\n\nENV = dict(os.environ)\n"}
            for name, text in cases.items():
                (Path(td) / name).parent.mkdir(exist_ok=True)
                (Path(td) / name).write_text(text)
            found = environ_at_import(_all_test_tree_files([Path(td)]))
            self.assertEqual(sorted((f.relative_to(td).as_posix(), line, what) for f, line, what in found),
                             [("a.py", 3, "copies"), ("b.py", 3, "copies"), ("c.py", 3, "copies"),
                              ("d.py", 5, "copies"), ("e.py", 4, "copies"), ("g.py", 3, "copies"),
                              ("j.py", 3, "writes"), ("k.py", 3, "writes"), ("l.py", 4, "writes"),
                              ("m.py", 3, "writes"), ("m.py", 4, "writes"),
                              ("o.py", 4, "copies"), ("o.py", 5, "copies"), ("o.py", 6, "copies"),
                              ("o.py", 7, "copies"), ("o.py", 8, "copies"),
                              ("p.py", 3, "copies"), ("p.py", 4, "writes"), ("p.py", 5, "writes"),
                              ("q.py", 3, "copies"), ("q.py", 4, "writes"), ("q.py", 5, "writes"),
                              ("r.py", 4, "writes"),
                              ("s.py", 4, "writes"), ("s.py", 6, "writes"),
                              ("sub/aa.py", 3, "copies"),
                              ("v.py", 3, "writes"), ("v.py", 4, "writes"), ("v.py", 5, "writes"),
                              ("y.py", 16, "copies"),
                              ("z.py", 4, "copies")])

    def test_the_environment_check_sees_each_form_it_names(self):
        # TJ-3972: each header and body `_at_import` takes, a class's
        # bases and keywords, a keyword-only default, `os.environb`, the
        # names `from os import *` and `import os.path` bind, `__enter__`.
        cases = {"headers.py": "import os\n\nif dict(os.environ):\n    pass\n"
                               "while os.environ.copy() and False:\n    pass\n"
                               "with open(os.environ.copy().get('X', os.devnull)):\n    pass\n"
                               "try:\n    pass\nexcept dict(os.environ).get('X', OSError):\n    pass\n"
                               "match dict(os.environ):\n    case {} if os.environ.copy():\n        pass\n",
                 "bodies.py": "import os\n\ntry:\n    import json\nexcept ImportError:\n    os.environ['A'] = '1'\n"
                              "else:\n    os.environ['B'] = '1'\nfinally:\n    os.environ['C'] = '1'\n"
                              "for _ in ():\n    pass\nelse:\n    os.environ['D'] = '1'\n"
                              "if False:\n    pass\nelse:\n    os.environ['E'] = '1'\n",
                 "defs.py": "import os\n\n\nclass A(dict(os.environ).__class__):\n    pass\n\n\n"
                            "class B(metaclass=type(os.environ.copy())):\n    pass\n\n\n"
                            "def f(*, env=os.environ.copy()):\n    return env\n",
                 "environb.py": "import os\n\nos.environb[b'X'] = b'1'\nY = os.environb.get(b'Y')\n",
                 "star.py": "from os import *\n\nenviron['X'] = '1'\nunsetenv('Y')\nZ = environb.copy()\n",
                 "ospath.py": "import os.path\n\nos.environ['X'] = '1'\n",
                 "enter.py": "import os\nfrom unittest import mock\n\nmock.patch.dict(os.environ, X='1').__enter__()\n"
                             "P = mock.patch.dict(os.environ, Y='1')\nP.__enter__()\n"}
        with tempfile.TemporaryDirectory() as td:
            for name, text in cases.items():
                (Path(td) / name).write_text(text)
            found = environ_at_import(_all_test_tree_files([Path(td)]))
            self.assertEqual(sorted((f.name, line, what) for f, line, what in found),
                             [("bodies.py", 6, "writes"), ("bodies.py", 8, "writes"), ("bodies.py", 10, "writes"),
                              ("bodies.py", 14, "writes"), ("bodies.py", 18, "writes"),
                              ("defs.py", 4, "copies"), ("defs.py", 8, "copies"), ("defs.py", 12, "copies"),
                              ("enter.py", 4, "writes"), ("enter.py", 6, "writes"),
                              ("environb.py", 3, "writes"),
                              ("headers.py", 3, "copies"), ("headers.py", 5, "copies"), ("headers.py", 7, "copies"),
                              ("headers.py", 11, "copies"), ("headers.py", 13, "copies"), ("headers.py", 14, "copies"),
                              ("ospath.py", 3, "writes"),
                              ("star.py", 3, "writes"), ("star.py", 4, "writes"), ("star.py", 5, "copies")])

    def test_only_the_setups_record_of_the_environment_is_allowed(self):
        # TJ-3962, TJ-3974: the record's statement alone, not the rest of
        # the setup, nor the same statement in another module; none
        # allowed when the setup records it twice.
        with tempfile.TemporaryDirectory() as td:
            setup, other = Path(td) / "_hermetic" / "__init__.py", Path(td) / "other.py"
            setup.parent.mkdir()
            setup.write_text("import os\n\nHOME = 'x'\nENVIRON = dict(os.environ)\nos.environ['X'] = '1'\n"
                             "COPY = os.environ.copy()\n")
            other.write_text("import os\n\nENVIRON = dict(os.environ)\n")
            found = environ_at_import([setup, other])
            self.assertIn((setup, 4, "copies"), found)
            self.assertEqual(environ_leaks(found, setup),
                             (4, [(setup, 5, "writes"), (setup, 6, "copies"), (other, 3, "copies")]))
            setup.write_text("import os\n\nENVIRON = dict(os.environ)\nENVIRON = dict(os.environ)\n")
            found = environ_at_import([setup])
            self.assertEqual(environ_leaks(found, setup), (None, [(setup, 3, "copies"), (setup, 4, "copies")]))

    def test_the_zone_test_kraken_template_sets_is_its_tests_alone(self):
        # TJ-3952: its setUpModule sets the zone, a zone already set kept;
        # the module cleanup a run calls after its tests puts it back.
        code = (_ZONE_AROUND.replace("ROOTS", repr([str(_paths.TESTS), str(_paths.SRC)]))
                .replace("MOD", repr(_KRAKEN)))
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=_paths.TESTS,
                           env=dict(os.environ), capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr)
        (unset, zone, after, ran, bad), (given, kept, left, _ran, _bad) = json.loads(r.stdout)
        self.assertEqual((unset, zone, after), (None, "America/Toronto", None))
        self.assertGreater(ran, 0)
        self.assertEqual(bad, 0, r.stdout)
        self.assertEqual((given, kept, left), ("Asia/Tokyo", "Asia/Tokyo", "Asia/Tokyo"))

    def test_the_setups_finder_serves_what_reads_home_at_import(self):
        """tests/_hermetic's finder runs the import of each product module
        that reads HOME at import under the setup's HOME (TJ-3889): a new
        one is added to `CacheHome.NAMES` (TJ-3916)."""
        import _hermetic
        self.assertEqual(set(_hermetic.CacheHome.NAMES), home_at_import(_paths.SRC))

    def test_what_reads_home_at_import(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            files = {"pkg/__init__.py": "", "pkg/cache.py": "import os\n\nF = os.path.expanduser('~/x')\n",
                     "pkg/lazy.py": "import os\n\n\ndef f():\n    return os.path.expanduser('~/x')\n",
                     "pkg/env.py": "import os\n\ntry:\n    H = os.environ['HOME']\nexcept KeyError:\n    H = ''\n",
                     "pkg/user.py": "from pkg import cache  # noqa: F401\n",
                     "pkg/pathy.py": "from pathlib import Path\n\nP = {'cache': Path.home() / 'x'}\n",
                     "pkg/klass.py": "import os\n\n\nclass C:\n    F = os.path.expandvars('$HOME/x')\n",
                     "pkg/dflt.py": "import os\n\n\ndef f(p=os.path.expanduser('~')):\n    return p\n",
                     "pkg/rel.py": "from . import user  # noqa: F401\n",
                     "pkg/late.py": "def f():\n    from pkg import cache  # noqa: F401\n"}
            for k, v in files.items():
                (src / k).parent.mkdir(parents=True, exist_ok=True)
                (src / k).write_text(v)
            self.assertEqual(home_at_import(src), {"pkg.cache", "pkg.env", "pkg.pathy", "pkg.klass", "pkg.dflt"})
            t = Path(td) / "tests"
            for k, v in {"test_a.py": "import pkg.rel  # noqa: F401\n", "test_b.py": "import pkg.lazy  # noqa: F401\n",
                         "_h.py": "from pkg.user import *  # noqa: F401,F403\n", "test_c.py": "import _h  # noqa: F401\n",
                         "test_d.py": "import pkg.late  # noqa: F401\n",
                         "test_f.py": "import pkg.pathy  # noqa: F401\n",
                         "sub/__init__.py": "", "sub/test_e.py": "from pkg import env  # noqa: F401\n"}.items():
                (t / k).parent.mkdir(parents=True, exist_ok=True)
                (t / k).write_text(v)
            targets, mods = reaching_test_modules([t], (src,))
            self.assertEqual(sorted(m for _t, m in mods), ["sub.test_e", "test_a", "test_c", "test_f"])
            self.assertEqual(given_home_bindings(t, "test_a", (src,), targets), ["pkg.cache"])
            self.assertEqual(given_home_bindings(t, "test_f", (src,), targets), ["pkg.pathy"])   # a Path in a dict
            (t / "_hermetic.py").write_text("import os\n\nos.environ['HOME'] = os.path.join("
                                            "os.path.dirname(os.path.abspath(__file__)), 'home')\n")
            (t / "test_a.py").write_text("import _hermetic  # noqa: F401\nimport pkg.rel  # noqa: F401\n")
            self.assertEqual(given_home_bindings(t, "test_a", (src,), targets), [])


if __name__ == "__main__":
    unittest.main()

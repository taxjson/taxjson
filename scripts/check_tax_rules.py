#!/usr/bin/env python3
"""check_tax_rules — tax-logic is the spec; the tests must cite it.

Every statement `tjs tax-logic` prints has a rule id (lib/tax_logic,
`catalog()`), a constant in src/taxjson/rules/ids.py (`R.CA_SL_02`).
Tests cite the ids they pin with the markers in tests/tax_rules
(`@rule(R.CA_SL_02)`, `@rule_absent(R.CA_SL_02, country="usa")`), in
tests/ and in the plugin packages' tests (packages/*/tests). This check reads the markers with the AST (it never
imports a test) and fails when:

  1. an id is malformed, unknown to tax-logic, or retired
     (tests/tax_rules/retired.txt);
  2. one @rule names ids of both countries, or a @rule_absent names the
     rule's own country;
  3. a test tagged for one country only names the other country's engine
     or project (USATaxRules, country="usa", --country usa,
     country = "usa" in a toml ... and the reverse);
  4. a rule has no @rule test and is not in the shrink-only baseline
     tests/tax_rules/baseline-unpinned.txt — or a baseline entry now has
     a test (delete the line), or names an id that is not one of the
     seed ids the baseline started from (tests/tax_rules/seed-ids.txt:
     a NEW rule can never be baselined, it needs its test);
  5. a partition rule (tax_logic.PARTITION_RULES) has no @rule_absent test
     and is not in tests/tax_rules/baseline-unpaired.txt (same ratchet);
  6. a [settings] key (lib/country.SETTING_COUNTRY) is named by no
     rule's `keys` and not listed in tax_logic.NON_RULE_SETTINGS, or a
     VARIANT_AXES key is not a known setting;
  7. a statement in the Canada section has a US- id or the reverse, or
     one id names two statements;
  8. a country-ownership table (lib/country) is incomplete: an entry
     with no valid owner or no *_WHY reason, a FLAG_COUNTRY flag that no
     CLI defines or that refuse_foreign_flags cannot read (_FLAG_ATTRS),
     a COMMAND_COUNTRY command that is not a `taxjson` subcommand, a
     [settings] key the code reads that SETTING_COUNTRY does not list, or
     a CLI option whose help calls it "Canada only" / "US only" that is
     in neither FLAG_COUNTRY nor FLAG_VALUE_COUNTRY, a source message
     that refuses an option for one country ("--x ... is Canada-only",
     "... does not apply with --country") for an option the tables do
     not own, or a PLAN_COUNTRY plan with no valid owner;
  9. rules/ids.py and the catalog differ: a statement with no constant,
     a constant no statement has, or a constant not named as its id
     (`-` as `_`);
 10. a string in src (src/taxjson, packages/*/src) outside rules/ids.py
     is a rule id, or an f-string, a format string or a %-string builds
     one, or src imports the ids other than as `from taxjson.rules import
     ids as R`: code names a rule by its constant (PLAN step 7);
 11. src cites (in a comment, a docstring, a message, a string or a
     statement's text) an id of a rule family that is no rule, or a
     retired one, or names a constant (`R.NAME`) that is no rule;
 12. a rule is cited nowhere in src outside the spec (lib/tax_logic.py and
     rules/ids.py): no `R.NAME`, and its id in no comment, docstring or
     message; and it is not in the baseline
     tests/tax_rules/baseline-uncited.txt — or a baseline entry now is
     cited (delete the line), or is not one of the ids that baseline
     started from (tests/tax_rules/seed-uncited.txt: a NEW rule is cited
     where the code applies it, never baselined). It only shrinks:
     UNCITED_MAX below is its size, lowered as its lines go. An engine
     rule (tax_logic.ENGINE_RULES: one an engine or characterisation
     decision record names, PLAN step 11) is cited only where code under
     engine/ or characterize/ names its constant (`R.NAME` through the
     module's own import of the ids: `engine_named`), never by a
     comment, a docstring or a string, and is never baselined (a
     constant in code that never runs passes here: tests/test_rule_ids.py
     asks a test to assert each on a record);
 13. a test's @rule or @rule_absent names a rule by a string
     (`@rule("CA-SL-02")`), not its constant (`@rule(R.CA_SL_02)`): a
     constant that is no rule fails at import, and a renamed id is found
     by its name (PLAN step 9). A constant reads as a rule only through
     the module's own import of taxjson.rules.ids, at module level, of a
     name nothing else in the module binds (`ids_bindings`): another
     base, or a bare name of the module's, is malformed (1).
 14. a marked test or test class that a later statement of the same name
     in its class or module replaces (a wrap such as `x = skip(x)` keeps
     it), a marked class not at module level, or a marked module-level
     function (unittest runs only TestCase methods): `collect` reads only
     module-level classes that run, so their markers pin nothing.

Usage: scripts/check_tax_rules.py [--summary]
       scripts/check_tax_rules.py --where RULE... (a rule, or a family: CA-SL)
Exit 0 when clean; 1 with one line per problem (--where: 1 on an unknown
rule or family, 2 with no rule).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taxjson.lib import country as C          # noqa: E402
from taxjson.lib import tax_logic as TL       # noqa: E402
from taxjson.rules import ids as RIDS         # noqa: E402

TESTS = ROOT / "tests"
RULES_DIR = TESTS / "tax_rules"
BASELINE = RULES_DIR / "baseline-unpinned.txt"
UNPAIRED = RULES_DIR / "baseline-unpaired.txt"
RETIRED = RULES_DIR / "retired.txt"
SEED = RULES_DIR / "seed-ids.txt"
UNCITED = RULES_DIR / "baseline-uncited.txt"
SEED_UNCITED = RULES_DIR / "seed-uncited.txt"
# baseline-uncited.txt's size: with the seed, it keeps the baseline from
# taking back a line once deleted (a rule cited, then not again).
UNCITED_MAX = 143
SRC = ROOT / "src" / "taxjson"
IDS_FILE = SRC / "rules" / "ids.py"
# The spec itself: its ids are where rules are stated, not applied.
SPEC_FILES = (SRC / "lib" / "tax_logic.py", IDS_FILE)
# Where an engine rule (tax_logic.ENGINE_RULES) is cited (12): the code that
# makes the decision records names it.
ENGINE_DIRS = (SRC / "engine", SRC / "characterize")
# An id's shape: a country, its family (SL, WASH, EST...), more parts. Checks
# 10 and 11 judge a match only when its family is a rule family, so a
# ticker, a form or a province ("US-NY-2025", "CA-ON") is not one.
CITE_RE = re.compile(r"(?<![\w-])(?:CA|US)-([A-Z0-9]+)(?:-[A-Z0-9]+)+(?![\w-])")
NAME_RE = re.compile(r"\bR\.((?:CA|US)_[A-Z0-9_]+)\b")
CONST_RE = re.compile(r"(?:CA|US)_[A-Z0-9_]+")
# A string's placeholders: an f-string's values, str.format's and %'s.
_HOLE_RE = re.compile(r"\{[^{}]*\}|%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdifr]")
# The one way src imports the ids, so `R.NAME` is every use (11, 12).
R_IMPORT = "from taxjson.rules import ids as R"
ID_RE = re.compile(r"^(CA|US)-[A-Z0-9]+(?:-[A-Z0-9]+)+$")

# A test tagged for one country must not name the other country's engine
# or project. (The runtime guard in tests/tax_rules catches the indirect
# cases: helpers, fixtures.)
_US_NAMES = [r"\bUSATaxRules\b",
             r"""\bcountry\s*=\s*\\?["'](?:usa|us|USA|US)\\?["']""",
             r"""["']--country["']\s*,\s*["'](?:usa|us)["']""",
             r"""get_tax_rules\(\s*["'](?:usa|us)["']"""]
_CA_NAMES = [r"\bCanadaTaxRules\b",
             r"""\bcountry\s*=\s*\\?["'](?:canada|ca|Canada|CA)\\?["']""",
             r"""["']--country["']\s*,\s*["'](?:canada|ca)["']""",
             r"""get_tax_rules\(\s*["'](?:canada|ca)["']"""]
FORBIDDEN = {C.CANADA: [re.compile(p) for p in _US_NAMES],
             C.USA: [re.compile(p) for p in _CA_NAMES]}


def read_ids(path: Path) -> List[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line.split()[0])
    return out


class Marked:
    """One test (method or class) and its markers."""

    def __init__(self, path: Path, node, cls=None):
        self.path, self.node, self.cls = path, node, cls
        self.rules: List[Tuple[Tuple[str, ...], int]] = []
        self.absent: List[Tuple[str, str, int]] = []

    @property
    def where(self) -> str:
        rel = self.path.relative_to(ROOT)
        name = (f"{self.cls.name}.{self.node.name}" if self.cls is not None
                and self.cls is not self.node else self.node.name)
        return f"{rel}:{self.node.lineno} {name}"


IDS_MODULE = "taxjson.rules.ids"
Binds = Tuple[Set[str], Dict[str, str]]


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
_OWN_SCOPE = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _scope_nodes(body):
    """The nodes a module's or a class's statements run in its own scope,
    in source order: compound statements entered; a function's, a lambda's
    and a comprehension's body not, nor a nested class's (a def's
    decorators and defaults and a class's bases are)."""
    stack = list(reversed(body))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, _FUNC):
            kids = n.decorator_list + n.args.defaults + [d for d in n.args.kw_defaults if d]
        elif isinstance(n, ast.ClassDef):
            kids = n.decorator_list + n.bases + [k.value for k in n.keywords]
        elif isinstance(n, _OWN_SCOPE):
            kids = []
        else:
            kids = list(ast.iter_child_nodes(n))
        stack.extend(reversed(kids))


def _binds(n) -> List[str]:
    """The names node N binds in its scope."""
    if isinstance(n, (*_FUNC, ast.ClassDef)):
        return [n.name]
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in n.names]
    if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
        return [n.id]
    if isinstance(n, ast.ExceptHandler) and n.name:
        return [n.name]
    if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
        return [n.name]
    if isinstance(n, ast.MatchMapping) and n.rest:
        return [n.rest]
    return []


def _taken(tree: ast.AST, accepted: Optional[Set[Tuple[int, str]]] = None) -> Set[str]:
    """The names a module binds where a marker could read them: in its own
    scope and in any class body (an assignment, def, class, import, `del`),
    by `global`, or by a walrus anywhere (TJ-3739); ACCEPTED's (node id,
    name) bindings left out. A `nonlocal` never names one (TJ-3738)."""
    out: Set[str] = set()
    accepted = accepted or set()
    for body in [tree.body] + [c.body for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]:
        for n in _scope_nodes(body):
            out.update(x for x in _binds(n) if (id(n), x) not in accepted)
    for n in ast.walk(tree):
        if isinstance(n, ast.Global):
            out.update(n.names)
        elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def ids_bindings(tree: ast.AST) -> Binds:
    """The names a module binds to the rule-id module (`from taxjson.rules
    import ids as R`, `import taxjson.rules.ids as R`), and the constants
    it imports from it (`from taxjson.rules.ids import CA_SL_02`, local
    name -> constant): an import in the module's own scope (not inside a
    function or a class), of a name nothing else in the module binds (no
    other assignment, def, class or import of it at module level or in a
    class body, no `global` of it, no walrus, no star import: `_taken`):
    what a marker reads is then the ids' value (TJ-3716)."""
    mods: Set[str] = set()
    consts: Dict[str, str] = {}
    accepted: Set[Tuple[int, str]] = set()
    for n in _scope_nodes(tree.body):
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == "taxjson.rules":
            for a in n.names:
                if a.name == "ids":
                    mods.add(a.asname or a.name)
                    accepted.add((id(n), a.asname or a.name))
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module == IDS_MODULE:
            for a in n.names:
                consts[a.asname or a.name] = a.name
                accepted.add((id(n), a.asname or a.name))
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name == IDS_MODULE and a.asname:
                    mods.add(a.asname)
                    accepted.add((id(n), a.asname))
    if not (mods or consts):
        return mods, consts
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
           for n in ast.walk(tree)):
        return set(), {}
    other = _taken(tree, accepted)
    return mods - other, {k: v for k, v in consts.items() if k not in other}


def _id_arg(a, binds: Binds) -> str:
    """A marker's argument as a rule id: a string, or a rules/ids
    constant read as its value: an attribute of a name the module binds
    to the ids module (`R.CA_SL_02`), or a name it imports from it
    (BINDS, `ids_bindings`). An unknown constant reads as `R.NAME`,
    anything else (another base, a bare name of the module's own: its
    value is not the id's) as its source in <>: both malformed, so a
    marker that pins nothing says so (TJ-3693)."""
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    mods, consts = binds
    if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id in mods:
        name = a.attr
    elif isinstance(a, ast.Name) and a.id in consts:
        name = consts[a.id]
    else:
        return f"<{ast.unparse(a)}>"
    if not CONST_RE.fullmatch(name):
        return f"<{ast.unparse(a)}>"
    v = getattr(RIDS, name, None)
    return v if isinstance(v, str) else f"R.{name}"


def _str_args(call: ast.Call, binds: Binds) -> List[str]:
    return [_id_arg(a, binds) for a in call.args]


def _accessor_or_stub(prev, n) -> bool:
    """PREV and the later N are one definition: PREV an `@overload` stub,
    or N a property's accessor (`@name.setter`)."""
    if any(_decorator_name(d) == "overload" for d in getattr(prev, "decorator_list", ())):
        return True
    return isinstance(n, _FUNC) and any(
        isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter")
        and isinstance(d.value, ast.Name) and d.value.id == n.name for d in n.decorator_list)


_BRANCHES = (ast.If, ast.Try, ast.TryStar, ast.For, ast.AsyncFor, ast.While, ast.With,
             ast.AsyncWith, ast.Match)


def _replaced(body) -> Dict[int, Tuple[ast.AST, bool]]:
    """The defs and classes in BODY's scope that a later binding in it
    replaces (a def or class of the name, an assignment to it, a `del`, an
    import as it), each with that binding and whether it sits in a branch
    (an `if`, a `try`, a loop, a `with`: TJ-3734): they never run, so their
    markers pin nothing (TJ-3701, TJ-3717, TJ-3718). A wrap keeps the
    object (`x = f(x)`), and so does a `del` of a class an earlier class
    takes as a base: its tests run in the subclass (TJ-3733)."""
    out: Dict[int, Tuple[ast.AST, bool]] = {}
    last: Dict[str, ast.AST] = {}
    keep: Set[int] = set()
    bases: Set[str] = set()
    for top in body:
        branch = isinstance(top, _BRANCHES)
        for n in _scope_nodes([top]):
            if isinstance(n, ast.ClassDef):
                bases.update(x.id for b in n.bases for x in ast.walk(b) if isinstance(x, ast.Name))
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                value = n.value if n.value is not None else ast.Constant(None)
                reads = {x.id for x in ast.walk(value)
                         if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for tg in targets:
                    keep.update(id(x) for x in ast.walk(tg) if isinstance(x, ast.Name) and (
                        x.id in reads or isinstance(n, ast.AugAssign) or n.value is None))
            elif isinstance(n, ast.Delete):
                keep.update(id(x) for tg in n.targets for x in ast.walk(tg)
                            if isinstance(x, ast.Name) and x.id in bases)
            if id(n) in keep:
                continue
            for name in _binds(n):
                prev = last.get(name)
                if isinstance(prev, (*_FUNC, ast.ClassDef)) and not _accessor_or_stub(prev, n):
                    out[id(prev)] = (n, branch)
                last[name] = n
    return out


def _defs(body) -> list:
    """The functions in a class or module body that run: one a later
    statement replaces does not (`_replaced`)."""
    gone = _replaced(body)
    return [n for n in body if isinstance(n, _FUNC) and id(n) not in gone]


def _decorators(node, binds: Binds) -> Tuple[list, list]:
    rules, absent = [], []
    for d in node.decorator_list:
        if not isinstance(d, ast.Call):
            continue
        f = d.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if name == "rule":
            rules.append((tuple(_str_args(d, binds)), d.lineno))
        elif name == "rule_absent":
            args = _str_args(d, binds)
            country = next((k.value.value for k in d.keywords
                            if k.arg == "country"
                            and isinstance(k.value, ast.Constant)), "")
            absent.append((args[0] if args else "", str(country), d.lineno))
    return rules, absent


def string_marker_problems(paths) -> List[str]:
    """13: each @rule / @rule_absent argument written as a string."""
    out: List[str] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # collect() says so
        rel = path.relative_to(ROOT).as_posix() if ROOT in path.parents else str(path)
        mods, _consts = ids_bindings(tree)
        # The module's own alias (TJ-3696); RID where its R is taken (TJ-3720).
        taken = _taken(tree)
        alias = sorted(mods)[0] if mods else next(
            (a for a in ("R", "RID", "RULE_IDS") if a not in taken), "RULE_IDS")
        how = "" if mods else f" (`from taxjson.rules import ids as {alias}` at the top)"
        for node in ast.walk(tree):
            for d in getattr(node, "decorator_list", ()):
                f = d.func if isinstance(d, ast.Call) else None
                name = (f.id if isinstance(f, ast.Name) else
                        f.attr if isinstance(f, ast.Attribute) else "")
                if name not in ("rule", "rule_absent"):
                    continue
                for a in d.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        out.append(f"{rel}:{a.lineno}: @{name}({a.value!r}): name the "
                                   f"rule by its constant, {alias}.{a.value.replace('-', '_')}"
                                   + how)
    return out


def _marked(node) -> bool:
    """A class or def that carries a marker (a class, on itself or a method)."""
    defs = [node] + ([n for n in node.body if isinstance(n, _FUNC)]
                     if isinstance(node, ast.ClassDef) else [])
    return any(_decorator_name(d) in ("rule", "rule_absent")
               for s in defs for d in s.decorator_list)


def _replaced_message(rel: str, name: str, node, by: Tuple[ast.AST, bool]) -> str:
    at, branch = by
    how = ("deleted" if isinstance(at, ast.Name) and isinstance(at.ctx, ast.Del) else
           "defined again" if isinstance(at, (*_FUNC, ast.ClassDef)) else "bound again")
    if branch:
        return (f"{rel}:{node.lineno}: {name} is {how} at line {at.lineno}, in a branch, so "
                f"this one may never run and its markers pin nothing then: one definition")
    return (f"{rel}:{node.lineno}: {name} is {how} at line {at.lineno}, so this one "
            f"never runs and its markers pin nothing: delete one")


def shadowed_problems(paths) -> List[str]:
    """14: a marked test or test class that a later statement of the same
    name in its class or module replaces (`_replaced`), a marked class not
    at module level, or a marked module-level function: collect() reads
    none of them, so their markers pin nothing (TJ-3701, TJ-3717, TJ-3718,
    TJ-3735, TJ-3736)."""
    out: List[str] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # collect() says so
        rel = path.relative_to(ROOT).as_posix() if ROOT in path.parents else str(path)
        top = {id(n) for n in _scope_nodes(tree.body) if isinstance(n, ast.ClassDef)}
        gone = _replaced(tree.body)
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if id(cls) not in top:
                if _marked(cls):
                    out.append(f"{rel}:{cls.lineno}: {cls.name} is not at module level: the "
                               f"check reads only module-level classes, so its markers pin "
                               f"nothing here: a module-level class or mixin")
                continue
            if id(cls) in gone:
                if _marked(cls):
                    out.append(_replaced_message(rel, cls.name, cls, gone[id(cls)]))
                continue
            marked_cls = any(_decorator_name(d) in ("rule", "rule_absent")
                             for d in cls.decorator_list)
            in_cls = _replaced(cls.body)
            for n in _scope_nodes(cls.body):
                if isinstance(n, _FUNC) and id(n) in in_cls and (marked_cls or _marked(n)):
                    out.append(_replaced_message(rel, f"{cls.name}.{n.name}", n, in_cls[id(n)]))
        for n in _scope_nodes(tree.body):
            if isinstance(n, _FUNC) and _marked(n):
                out.append(f"{rel}:{n.lineno}: {n.name} is a module-level function: unittest "
                           f"runs only a TestCase's methods, so its markers pin nothing: "
                           f"a method of a module-level class")
    return out


def _decorator_name(d) -> str:
    f = d.func if isinstance(d, ast.Call) else d
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def collect(paths) -> List[Marked]:
    out: List[Marked] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            print(f"tax-rules: cannot parse {path}: {e}", file=sys.stderr)
            continue
        binds = ids_bindings(tree)
        gone = _replaced(tree.body)
        for node in _scope_nodes(tree.body):
            # Only the classes unittest finds and runs: at module level, not
            # replaced by a later statement (check 14 names the others).
            if isinstance(node, ast.ClassDef) and id(node) not in gone:
                crules, cabsent = _decorators(node, binds)
                if crules or cabsent:
                    m = Marked(path, node, node)
                    m.rules, m.absent = crules, cabsent
                    out.append(m)
                for sub in _defs(node.body):
                    r, a = _decorators(sub, binds)
                    if r or a or crules or cabsent:
                        m = Marked(path, sub, node)
                        m.rules = r + crules
                        m.absent = a + cabsent
                        out.append(m)
        # No module-level function: unittest never runs one (check 14; TJ-3735).
    return out


_ONE_COUNTRY_HELP = re.compile(
    r"\b(?:Canada|US|USA|United States)[ -]only\b", re.IGNORECASE)
_SETTINGS_READ = re.compile(
    r"""settings(?:\(\))?(?:\.get\(|\[)\s*["']([a-z_]+)["']""")


def _cli_options() -> Dict[str, List[str]]:
    """{--option: [help text, ...]} of every argparse option defined
    under src/taxjson (read with the AST; help text only when it is a
    literal)."""
    out: Dict[str, List[str]] = {}
    for path in sorted((ROOT / "src" / "taxjson").rglob("*.py")):
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                continue
            opts = [a.value for a in node.args
                    if isinstance(a, ast.Constant)
                    and isinstance(a.value, str) and a.value.startswith("--")]
            help_txt = ""
            for kw in node.keywords:
                if kw.arg == "help":
                    try:
                        help_txt = str(ast.literal_eval(kw.value))
                    except (ValueError, SyntaxError):
                        help_txt = ""
            for o in opts:
                out.setdefault(o, []).append(help_txt)
    return out


def _taxjson_subcommands() -> Set[str]:
    """The `taxjson` subcommand names (add_parser("name", ...) literals in
    the cli/ group modules, each in its `register`)."""
    src = "\n".join(p.read_text(encoding="utf-8")
                    for p in (ROOT / "src" / "taxjson" / "cli").glob("*.py"))
    return set(re.findall(r"""add_parser\(\s*["']([a-z0-9-]+)["']""", src))


# A refusal message for one option, possibly over adjacent string
# literals ("..." "..."): an ad-hoc gate the tables must own (A2-0719).
_GATE_MSG_RE = re.compile(
    r'(--[a-z][a-z0-9-]+)(?:[^\n"]|"\s*\n\s*[rf]?"){0,120}?'
    r'(?:is (?:Canada|United States|US|USA)-only'
    r'|does not apply (?:with|to) (?:--)?country)')


def ownership_problems() -> List[str]:
    """Problems with lib/country's ownership tables (check 8)."""
    problems: List[str] = []
    valid = set(C.COUNTRIES) | {C.BOTH}
    tables = [("SETTING_COUNTRY", C.SETTING_COUNTRY, C.SETTING_WHY),
              ("CONFIG_COUNTRY", C.CONFIG_COUNTRY, C.CONFIG_WHY),
              ("FLAG_COUNTRY", C.FLAG_COUNTRY, C.FLAG_WHY),
              ("FLAG_VALUE_COUNTRY", C.FLAG_VALUE_COUNTRY,
               C.FLAG_VALUE_WHY),
              ("COMMAND_COUNTRY", C.COMMAND_COUNTRY, C.COMMAND_WHY),
              ("PROJECT_FILE_COUNTRY", C.PROJECT_FILE_COUNTRY,
               C.PROJECT_FILE_WHY)]
    for name, table, why in tables:
        for key, owner in table.items():
            if owner not in valid:
                problems.append(f"{name}[{key!r}]: owner {owner!r} is not "
                                f"canada, usa or both")
            if owner in C.COUNTRIES and not why.get(key):
                problems.append(f"{name}[{key!r}] has no reason in the "
                                f"matching *_WHY table")
    options = _cli_options()
    flags = set(C.FLAG_COUNTRY) | {f for f, _v in C.FLAG_VALUE_COUNTRY}
    for flag in sorted(flags):
        if flag not in options:
            problems.append(f"FLAG_COUNTRY: {flag} is not an option of any "
                            f"CLI under src/taxjson")
        if flag not in C._FLAG_ATTRS:
            problems.append(f"FLAG_COUNTRY: {flag} is missing from "
                            f"country._FLAG_ATTRS, so refuse_foreign_flags "
                            f"never sees it")
    for opt, helps in sorted(options.items()):
        if opt in flags:
            continue
        if any(_ONE_COUNTRY_HELP.search(h) for h in helps):
            problems.append(f"{opt}: its help says it is one country's "
                            f"only, but it is in neither FLAG_COUNTRY nor "
                            f"FLAG_VALUE_COUNTRY (lib/country)")
    subs = _taxjson_subcommands()
    for cmd in sorted(C.COMMAND_COUNTRY):
        base = cmd.split(":", 1)[0]
        if base not in subs:
            problems.append(f"COMMAND_COUNTRY: {cmd} — `taxjson {base}` is "
                            f"not a subcommand")
    read: Set[str] = set()
    for path in (ROOT / "src" / "taxjson").rglob("*.py"):
        read |= set(_SETTINGS_READ.findall(
            path.read_text(encoding="utf-8")))
    for key in sorted(read - set(C.SETTING_COUNTRY)):
        problems.append(f"[settings] {key} is read by the code but is not "
                        f"in SETTING_COUNTRY (lib/country)")
    for key, owner in C.PLAN_COUNTRY.items():
        if owner not in valid:
            problems.append(f"PLAN_COUNTRY[{key!r}]: owner {owner!r} is "
                            f"not canada, usa or both")
    for path in sorted((ROOT / "src" / "taxjson").rglob("*.py")):
        if path.name == "country.py":
            continue
        for m in _GATE_MSG_RE.finditer(path.read_text(encoding="utf-8")):
            opt = m.group(1)
            if opt != "--country" and opt in options and opt not in flags:
                problems.append(f"{path.relative_to(ROOT)}: refuses {opt} "
                                f"for one country, but lib/country does "
                                f"not own it (FLAG_COUNTRY / "
                                f"FLAG_VALUE_COUNTRY)")
    return problems


def ids_module_problems(known: Set[str], module=RIDS) -> List[str]:
    """9: rules/ids.py holds the catalog's ids, one constant each."""
    out = []
    consts = {k: v for k, v in vars(module).items()
              if CONST_RE.fullmatch(k) and isinstance(v, str)}
    for k, v in sorted(consts.items()):
        if k != v.replace("-", "_"):
            out.append(f"rules/ids.py: {k} = \"{v}\": a constant is named "
                       f"as its id, - as _ ({v.replace('-', '_')})")
        if v not in known:
            out.append(f"rules/ids.py: {k} = \"{v}\" is not a tax-logic "
                       f"rule: delete it (a retired id goes in retired.txt)")
    for rid in sorted(known - set(consts.values())):
        out.append(f"{rid}: tax-logic states it but src/taxjson/rules/"
                   f"ids.py has no constant: add {rid.replace('-', '_')} = "
                   f"\"{rid}\" in its family")
    return out


def _families(ids: Iterable[str]) -> Set[str]:
    return {i.split("-")[1] for i in ids}


def _builds_an_id(shape: str, ids: Set[str]) -> bool:
    """A string with placeholders (`{}`, `{:02d}`, `%s`) that, filled, can be
    one of `ids`: a whole-string match with a letter pair of its own."""
    if not re.search(r"[A-Z]{2}", _HOLE_RE.sub("", shape)):
        return False
    out, pos = [], 0
    for m in _HOLE_RE.finditer(shape):
        out += [re.escape(shape[pos:m.start()]), "[A-Z0-9]+"]   # one segment
        pos = m.end()
    out.append(re.escape(shape[pos:]))
    # One search over every id, a line each ([A-Z0-9] never crosses one).
    return re.search("^" + "".join(out) + "$", "\n".join(sorted(ids)),
                     re.MULTILINE) is not None


def _src_files() -> List[Path]:
    """src/taxjson and the plugin packages' sources (packages/*/src)."""
    files = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
    files += sorted(p for p in (ROOT / "packages").glob("*/src/**/*.py")
                    if "__pycache__" not in p.parts)
    return files


def _literal_problems(rel: str, tree, known: Set[str], ids: Set[str]) -> List[str]:
    """10: a rule id written, or built, as a string. A retired id, or one
    of a rule family that is no rule, is check 11's (a citation)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v in known:
                out.append(f"{rel}:{node.lineno}: the rule id \"{v}\" as a "
                           f"string: name it R.{v.replace('-', '_')} "
                           f"({R_IMPORT})")
            elif _HOLE_RE.search(v) and _builds_an_id(v, ids):
                out.append(f"{rel}:{node.lineno}: \"{v}\" builds a rule id: "
                           f"pick the constant from a table of them "
                           f"(rules/ids.py) instead")
        elif (isinstance(node, ast.JoinedStr)
              and any(isinstance(v, ast.FormattedValue) for v in node.values)):
            shape = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                            for v in node.values)
            if _builds_an_id(shape, ids):
                out.append(f"{rel}:{node.lineno}: f\"{shape}\" builds a rule "
                           f"id: pick the constant from a table of them "
                           f"(rules/ids.py) instead")
    return out


def _import_problems(rel: str, tree) -> List[str]:
    """10: the ids imported another way than R_IMPORT (an alias, a name
    from the module, the module itself), which `R.NAME` would not see."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names
                   if a.name in ("taxjson.rules", "taxjson.rules.ids")]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            last = mod.rpartition(".")[2]
            if node.level:
                bad = ([mod] if last == "ids" else
                       [a.name for a in node.names if a.name == "ids"]
                       if last == "rules" or not mod else [])
            elif mod == "taxjson.rules.ids":
                bad = [mod]
            elif mod == "taxjson.rules":
                bad = [a.name for a in node.names
                       if a.name == "ids" and a.asname != "R"]
            else:
                bad = []
        else:
            continue
        if bad:
            out.append(f"{rel}:{node.lineno}: imports the rule ids another "
                       f"way: write `{R_IMPORT}` (the checker reads R.NAME)")
    return out


def source_id_problems(known: Set[str], retired: Set[str],
                       files: Optional[Iterable[Path]] = None
                       ) -> Tuple[List[str], Dict[str, List[str]]]:
    """10 and 11 over src's files; and where each rule is cited outside
    the spec (12): {id: [file, ...]}."""
    problems: List[str] = []
    cited: Dict[str, List[str]] = {}
    ids = known | retired
    families = _families(ids)
    retired_names = {r.replace("-", "_"): r for r in retired}
    for path in (_src_files() if files is None else files):
        rel = (path.relative_to(ROOT).as_posix() if ROOT in path.parents
               else path.as_posix())
        text = path.read_text(encoding="utf-8")
        spec = path in SPEC_FILES
        if path != IDS_FILE:
            tree = ast.parse(text)
            problems.extend(_literal_problems(rel, tree, known, ids))
            problems.extend(_import_problems(rel, tree))
        for m in CITE_RE.finditer(text):
            rid = m.group(0)
            if m.group(1) not in families:
                continue
            line = text.count("\n", 0, m.start()) + 1
            if rid in retired:
                problems.append(f"{rel}:{line}: cites {rid}, a retired rule "
                                f"(tests/tax_rules/retired.txt): cite the "
                                f"statement that replaced it")
            elif rid not in known:
                problems.append(f"{rel}:{line}: cites {rid}, which is no "
                                f"tax-logic rule")
            elif not spec:
                cited.setdefault(rid, []).append(rel)
        for m in NAME_RE.finditer(text):
            name = m.group(1)
            rid = getattr(RIDS, name, None)
            if rid is not None and not isinstance(rid, str):
                continue            # a table of ids (CA_EST_PROVINCE_TABLES)
            line = text.count("\n", 0, m.start()) + 1
            if name in retired_names:
                problems.append(f"{rel}:{line}: R.{name} names "
                                f"{retired_names[name]}, a retired rule: name "
                                f"the statement that replaced it")
            elif not isinstance(rid, str) or rid not in known:
                problems.append(f"{rel}:{line}: R.{name} is no rule id "
                                f"(src/taxjson/rules/ids.py)")
            elif not spec:
                cited.setdefault(rid, []).append(rel)
    return problems, cited


def engine_named(files: Optional[Iterable[Path]] = None) -> Dict[str, List[str]]:
    """12, for an engine rule: where engine and characterize code
    (ENGINE_DIRS) names a rule by its constant, an `R.NAME` attribute on
    the module's own import of the ids (`ids_bindings`), read with the
    AST, so a comment, a docstring or a string names none: {id: [file,
    ...]}."""
    out: Dict[str, List[str]] = {}
    if files is None:
        files = sorted(p for d in ENGINE_DIRS for p in d.rglob("*.py")
                       if "__pycache__" not in p.parts)
    for path in files:
        rel = (path.relative_to(ROOT).as_posix() if ROOT in path.parents
               else path.as_posix())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods, _consts = ids_bindings(tree)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id in mods):
                rid = getattr(RIDS, n.attr, None)
                if isinstance(rid, str):
                    out.setdefault(rid, []).append(rel)
    return out


def uncited_problems(known: Set[str], cited: Dict[str, List[str]],
                     baseline: List[str], seed: Set[str],
                     cap: int = UNCITED_MAX, engine: Iterable[str] = (),
                     named: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """12: every rule cited in src, or in the shrink-only baseline; an
    engine rule (`engine`, tax_logic.ENGINE_RULES) only where engine code
    names its constant (`named`, `engine_named`), and never baselined."""
    engine = set(engine)
    named = named or {}
    cited = {rid: w for rid, w in cited.items() if rid not in engine}
    cited.update((rid, named[rid]) for rid in engine if rid in named)
    out = [f"ENGINE_RULES: {rid} is not a tax-logic rule"
           for rid in sorted(engine - known)]
    for rid in baseline:
        if rid not in known:
            out.append(f"baseline-uncited.txt: {rid} is not a tax-logic "
                       f"rule (delete the line)")
        elif rid in engine:
            out.append(f"baseline-uncited.txt: {rid} is an engine rule "
                       f"(tax_logic.ENGINE_RULES): the engine names it where "
                       f"it makes the record, never baselined — delete the "
                       f"line")
        elif rid in cited:
            out.append(f"baseline-uncited.txt: {rid} is now cited "
                       f"({cited[rid][0]}) — delete the line (the baseline "
                       f"only shrinks)")
        elif rid not in seed:
            out.append(f"baseline-uncited.txt: {rid} was not in the seed "
                       f"(seed-uncited.txt) — a new rule is cited where the "
                       f"code applies it, not baselined")
    if len(baseline) > cap:
        out.append(f"baseline-uncited.txt has {len(baseline)} ids, more than "
                   f"UNCITED_MAX ({cap}): it only shrinks — cite the rule "
                   f"again where the code applies it")
    elif len(baseline) < cap:
        out.append(f"baseline-uncited.txt has {len(baseline)} ids: lower "
                   f"UNCITED_MAX in scripts/check_tax_rules.py to "
                   f"{len(baseline)}")
    for rid in sorted(known - set(cited) - set(baseline)):
        if rid in engine:
            out.append(f"{rid} is an engine rule (tax_logic.ENGINE_RULES) "
                       f"that no engine or characterize code names: name "
                       f"R.{rid.replace('-', '_')} where the record is made "
                       f"(a comment, a docstring or a string does not count)")
            continue
        out.append(f"{rid} is cited nowhere in src outside the spec: name it "
                   f"(R.{rid.replace('-', '_')}, or {rid} in a comment) "
                   f"where the code applies it")
    return out


def citation_problems(known: Set[str], retired: Set[str],
                      problems: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """9 to 12, added to `problems`; returns where each rule is cited from
    code and the uncited baseline."""
    problems.extend(ids_module_problems(known))
    src_problems, cited = source_id_problems(known, retired)
    problems.extend(src_problems)
    named = engine_named()
    uncited = read_ids(UNCITED)
    problems.extend(uncited_problems(known, cited, uncited,
                                     set(read_ids(SEED_UNCITED)),
                                     engine=TL.ENGINE_RULES, named=named))
    # Where each rule counts as cited: an engine rule, by the engine alone.
    cited = {rid: w for rid, w in cited.items() if rid not in TL.ENGINE_RULES}
    cited.update((rid, named[rid]) for rid in TL.ENGINE_RULES if rid in named)
    return cited, uncited


def _summary(known, pinned, baseline, paired, unpaired, cited, uncited,
             problems) -> str:
    return (f"tax-rules: {len(known)} rules, {len(set(pinned) & known)} pinned "
            f"by a test, {len(set(baseline) & known)} in the unpinned baseline; "
            f"{len(TL.PARTITION_RULES)} partition rules, "
            f"{len(set(paired) & set(TL.PARTITION_RULES))} with a "
            f"@rule_absent pair, {len(unpaired)} in the unpaired "
            f"baseline; {len(set(cited) & known)} cited in src, "
            f"{len(set(uncited) & known)} in the uncited baseline, "
            f"{len(TL.ENGINE_RULES)} engine rules, "
            f"{len(set(cited) & set(TL.ENGINE_RULES))} named by the engine; "
            f"{len(problems)} problem(s)")


def _where_key(where: str):
    path, _, rest = where.partition(":")
    return (path, int(rest.split(" ", 1)[0]))


def _where(asked: List[str], known: Set[str], pinned, paired,
           problems: List[str]) -> int:
    """--where: the tests that pin each asked rule, or each rule of an
    asked family, and those pairing it through @rule_absent (a test of a
    tests/_support mixin once, where it is defined, not once per class
    built on it: TJ-3988); then any
    problem found before the lists (checks 1 to 3, 7, 13 and 14, and a
    retired id tax-logic still states: a marker naming no rule is in no
    list, a string marker or a shadowed test makes the list doubtful),
    exit 1 (TJ-3673, TJ-3721)."""
    def matches(a: str, rid: str) -> bool:
        return rid == a or rid.startswith(a + "-")
    rc = 0
    for a in asked:
        if not any(matches(a, k) for k in known):
            print(f"tax-rules: {a}: tax-logic states no such rule or family")
            rc = 1
    for rid in sorted(k for k in known if any(matches(a, k) for a in asked)):
        pins = sorted(pinned.get(rid, []), key=_where_key)
        pairs = sorted(paired.get(rid, []), key=_where_key)
        print(f"{rid}: {len(pins)} pinning"
              + (f", {len(pairs)} absent" if pairs else ""))
        for w in pins:
            print(f"  {w}")
        for w in pairs:
            print(f"  absent: {w}")
    for p in problems:
        print(f"tax-rules: {p}")
    return 1 if problems else rc


def _where_args(argv: List[str]) -> Optional[List[str]]:
    """--where RULE...: the tests that pin each rule (and those pairing it
    through @rule_absent), wherever they sit; a family (CA-SL) lists its
    rules'. The index of the tests by rule (PLAN.md step 9), answered once
    the markers are read, before the other checks. None without --where;
    [] (said) when no rule follows it. Options are not rules."""
    if "--where" not in argv:
        return None
    asked = [a for a in argv[argv.index("--where") + 1:] if not a.startswith("-")]
    if not asked:
        print("tax-rules: --where needs a rule id or a family "
              "(--where CA-SL-02, --where CA-SL)")
    return asked


# Check 1's advice for a constant it cannot read (TJ-3720).
_READABLE = (": name the rule as R.CA_XX_01, R a module-level `from taxjson.rules import ids "
             "as R` that nothing else binds")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    asked = _where_args(argv)
    if asked == []:
        return 2
    problems: List[str] = []
    catalog = TL.catalog()
    known = set(catalog)
    retired = set(read_ids(RETIRED))
    seed = set(read_ids(SEED))

    # 7. prefix matches the section's country
    for c, prefix in ((C.CANADA, "CA-"), (C.USA, "US-")):
        for st in TL.variants(c):
            for _t, rules in TL.rule_sections(c, st):
                for r in rules:
                    if not r.id.startswith(prefix):
                        problems.append(f"{r.id} is in the {c} section of "
                                        f"tax-logic but has no {prefix} prefix")
                    if not ID_RE.match(r.id):
                        problems.append(f"{r.id}: malformed rule id")
            # One id per claim: an id that names two statements in one
            # rendering (re-audit: US-RPT-09, CA-RPT-13).
            seen: Dict[str, int] = {}
            prev = None
            for _t, rules in TL.rule_sections(c, st):
                for r in rules:
                    if r.id != prev:
                        seen[r.id] = seen.get(r.id, 0) + 1
                    prev = r.id
            for rid, n in seen.items():
                msg = (f"{rid} names {n} statements in the {c} section "
                       f"of tax-logic (one id per claim)")
                if n > 1 and msg not in problems:
                    problems.append(msg)
    for rid in known & retired:
        problems.append(f"{rid} is in retired.txt but tax-logic still "
                        f"states it")

    # The plugin packages' tests (packages/*/tests) pin rules too.
    files = sorted(p for p in TESTS.rglob("*.py")
                   if RULES_DIR not in p.parents)
    files += sorted(p for p in (ROOT / "packages").glob("*/tests/**/*.py"))
    marked = collect(files)
    problems += string_marker_problems(files)  # 13
    problems += shadowed_problems(files)  # 14
    pinned: Dict[str, List[str]] = {}
    paired: Dict[str, List[str]] = {}
    for m in marked:
        # Class-level entries are listed once for the class itself and
        # once per method; check ids and the static country scan on the
        # methods (and on a marked class with no methods).
        is_class_entry = m.cls is m.node
        countries: Set[str] = set()
        for ids, line in m.rules:
            cs = set()
            for rid in ids:
                if not ID_RE.match(rid):
                    problems.append(f"{m.where}: @rule({rid!r}): malformed id"
                                    + (_READABLE if rid.startswith("<") else ""))
                    continue
                if rid in retired:
                    problems.append(f"{m.where}: @rule({rid!r}) names a "
                                    f"retired id")
                elif rid not in known:
                    problems.append(f"{m.where}: @rule({rid!r}): tax-logic "
                                    f"states no such rule")
                cs.add(TL.rule_country(rid))
                if not is_class_entry:
                    pinned.setdefault(rid, []).append(m.where)
            if len(cs) > 1:
                problems.append(f"{m.where}: one @rule{ids} names ids of "
                                f"both countries — one @rule per country")
            countries |= cs
        for rid, ctry, line in m.absent:
            if rid not in known:
                problems.append(f"{m.where}: @rule_absent({rid!r}): "
                                f"tax-logic states no such rule")
                continue
            try:
                cc = C.canonical_country(ctry)
            except C.CountryError:
                problems.append(f"{m.where}: @rule_absent({rid!r}, "
                                f"country={ctry!r}): unknown country")
                continue
            if cc == TL.rule_country(rid):
                problems.append(f"{m.where}: @rule_absent({rid!r}, "
                                f"country={ctry!r}) names the rule's own "
                                f"country")
            if not is_class_entry:
                paired.setdefault(rid, []).append(m.where)
            countries |= {cc, TL.rule_country(rid)}
        # 3. static cross-country scan of a single-country test
        if len(countries) == 1 and not is_class_entry:
            own = next(iter(countries))
            seg = ast.get_source_segment(m.path.read_text(encoding="utf-8"),
                                         m.node) or ""
            for pat in FORBIDDEN[own]:
                hit = pat.search(seg)
                if hit:
                    problems.append(
                        f"{m.where} is tagged {own}-only but names "
                        f"{hit.group(0)!r} — use @rule_absent(..., "
                        f"country=...) for a dual-country test")
                    break
    if asked is not None:
        return _where(asked, known, pinned, paired, problems)

    # 4. unpinned rules vs the shrink-only baseline
    baseline = read_ids(BASELINE)
    for rid in baseline:
        if rid not in known:
            problems.append(f"baseline-unpinned.txt: {rid} is not a "
                            f"tax-logic rule (delete the line)")
        elif rid in pinned:
            problems.append(f"baseline-unpinned.txt: {rid} now has a test "
                            f"({pinned[rid][0]}) — delete the line (the "
                            f"baseline only shrinks)")
        elif rid not in seed:
            problems.append(f"baseline-unpinned.txt: {rid} was not in the "
                            f"seed (seed-ids.txt) — a new rule needs a "
                            f"test, not a baseline entry")
    for rid in sorted(known - set(pinned) - set(baseline)):
        problems.append(f"{rid} has no test (add @rule(R.{rid.replace('-', '_')}) "
                        f"to the test that pins it)")

    # 5. partition rules need a @rule_absent pair
    unpaired = read_ids(UNPAIRED)
    for rid in unpaired:
        if rid not in TL.PARTITION_RULES:
            problems.append(f"baseline-unpaired.txt: {rid} is not in "
                            f"tax_logic.PARTITION_RULES (delete the line)")
        elif rid in paired:
            problems.append(f"baseline-unpaired.txt: {rid} now has a "
                            f"@rule_absent test ({paired[rid][0]}) — "
                            f"delete the line")
        elif rid not in seed:
            problems.append(f"baseline-unpaired.txt: {rid} was not in the "
                            f"seed — a new partition rule needs its "
                            f"@rule_absent test")
    for rid in sorted(TL.PARTITION_RULES):
        if rid not in known:
            problems.append(f"PARTITION_RULES: {rid} is not a tax-logic "
                            f"rule")
        elif rid not in paired and rid not in unpaired:
            problems.append(f"{rid} is a partition rule with no "
                            f"@rule_absent test (the same book under the "
                            f"other country)")

    # 6. settings coverage
    named: Set[str] = set()
    for r in catalog.values():
        named |= set(r.keys)
    for key in C.SETTING_COUNTRY:
        if key not in named and key not in TL.NON_RULE_SETTINGS:
            problems.append(f"[settings] {key} changes behaviour but no "
                            f"tax-logic rule names it (Rule.keys) and it "
                            f"is not in NON_RULE_SETTINGS")
    for c, axes in TL.VARIANT_AXES.items():
        for key in axes:
            if key not in C.SETTING_COUNTRY:
                problems.append(f"VARIANT_AXES[{c}] {key}: not a known "
                                f"[settings] key (lib/country)")

    # 8. country-ownership tables complete (lib/country docstring)
    problems.extend(ownership_problems())

    # 9 to 12. rules/ids.py, and how src names and cites the rules
    cited, uncited = citation_problems(known, retired, problems)

    for p in problems:
        print(f"tax-rules: {p}")
    if "--summary" in argv or not problems:
        print(_summary(known, pinned, baseline, paired, unpaired, cited,
                       uncited, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

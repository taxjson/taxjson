#!/usr/bin/env python3
"""check_annotations_only: the proof that a typing commit changes no behaviour
(PLAN.md step 10 T0; the design: audits/step10-design/t0-design.md).

Usage: scripts/check_annotations_only.py --check BASE HEAD [--repo=PATH] [--trees]
       scripts/check_annotations_only.py --explain FILE

--check is run as BASE's copy (from git, isolated: a typing commit cannot
weaken its own proof), as scripts/move_defs.py --check is:

    T=$(mktemp -d) && git --no-replace-objects archive BASE \\
      scripts/check_annotations_only.py | tar -x -C "$T" && \\
      python3 -I -S "$T/scripts/check_annotations_only.py" --check BASE HEAD --repo=.

Run from a checkout it exports BASE's copy and runs that. Each module HEAD
changes is in one of two modes: future (BASE lacks `from __future__ import
annotations`, HEAD adds exactly that statement, directly after the
docstring, and nothing else) or typing (both have it). A module BASE lacks
it for and HEAD changes otherwise is refused: add the future import first,
in its own commit. Removing it is refused. The stages:

  files     every changed path is a .py under src/taxjson/, modified (not
            added, deleted or re-moded); pyproject.toml, where only the
            ratchet shrank (the [tool.mypy] override with ignore_errors
            lists a subset of BASE's modules); backlog.md, or a .md under
            docs/ or audits/. tests/test_type_checks.py is refused;
  ast       the whole module, bodies too. Future mode: HEAD less the import
            is BASE. Typing mode: both sides normalized (signature
            annotations; AnnAssign by scope, and in a class body by the
            class's kind; a function's bare `x: T`, whose scope the bytecode
            stage holds; TYPE_CHECKING blocks; the module-level TypeAlias
            statements HEAD adds, where BASE binds no such name (BASE's are
            compared as assignments); typing casts; module-level imports from
            typing and collections.abc) are equal. An AnnAssign or a
            TYPE_CHECKING block HEAD adds or changes in a class defined inside
            a function is refused: it can make a name the class body reads
            from the function a class-local one;
  bytecode  each function's code (compiled with signature annotations and
            casts stripped, local AnnAssigns and TYPE_CHECKING blocks kept)
            and the module's and each class body's (compiled from the ast
            stage's normalization), matched by qualname, field by field, line
            tables aside;
  purity    each TypeAlias value HEAD adds is a type expression (of the
            constants, only a string forward reference or None stands alone),
            safe to evaluate at import on Python 3.12; in future mode, each
            annotation BASE evaluates and HEAD will not runs no code;
  bindings  the typing names BASE binds HEAD binds; a new one is in Python
            3.12's typing or collections.abc, shadows nothing and is read by
            none of BASE's code; no new typing_extensions import; a stripped
            cast is typing's; a new TypeAlias's name is bound nowhere else;
  snapshot  each changed module imported from each tree in a fresh
            `-I -S` interpreter (HOME a new empty folder, offline), every
            module of the package from that tree's src: the module's names,
            its classes (MRO, members, dataclass params and fields with their
            kinds) and its functions (defaults, signature without
            annotations) are BASE's, the TypeAliases HEAD adds aside. It runs
            HEAD's code, so only once every stage before it passed.

The last line is `annotations-only: PASS`, or `annotations-only: FAIL
(stages)`. Exit 0 only on PASS; 1 on a refusal; 2 on wrong arguments, a
Python older than 3.12 or a repository git cannot show as it is. --trees
lets BASE and HEAD be trees (a synthetic repository's; no ancestry).
--explain prints FILE's AST as the typing mode normalizes it. Standard
library only.
"""
from __future__ import annotations

import ast
import builtins
import copy
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

TOOL = "scripts/check_annotations_only.py"
MIN_PYTHON = (3, 12)
PACKAGE_DIR = "src/taxjson/"
TYPE_CHECKS = "tests/test_type_checks.py"
STAGES = ("files", "ast", "bytecode", "purity", "bindings", "snapshot")
SHOWN = 10                      # refusals listed per stage
TYPING_MODULES = ("typing", "collections.abc")
MARKER = "<annotation>"         # what a kept annotation is replaced by
FUTURE_FLAG = 0x1000000         # CO_FUTURE_ANNOTATIONS (__future__.annotations.compiler_flag)
CO_OPTIMIZED = 0x1              # a function's code (not a module's or a class body's)
TIMEOUT = 900

# typing.__all__ on Python 3.12.3 (/usr/bin/python3.12): what `from typing
# import X` finds on Python 3.12, the project's floor. 3.13 adds
# get_protocol_members, is_protocol, NoDefault, ReadOnly and TypeIs.
TYPING_312 = frozenset({
    "AbstractSet", "Annotated", "Any", "AnyStr", "AsyncContextManager", "AsyncGenerator",
    "AsyncIterable", "AsyncIterator", "Awaitable", "BinaryIO", "ByteString", "Callable",
    "ChainMap", "ClassVar", "Collection", "Concatenate", "Container", "ContextManager",
    "Coroutine", "Counter", "DefaultDict", "Deque", "Dict", "Final", "ForwardRef", "FrozenSet",
    "Generator", "Generic", "Hashable", "IO", "ItemsView", "Iterable", "Iterator", "KeysView",
    "List", "Literal", "LiteralString", "Mapping", "MappingView", "Match", "MutableMapping",
    "MutableSequence", "MutableSet", "NamedTuple", "Never", "NewType", "NoReturn",
    "NotRequired", "Optional", "OrderedDict", "ParamSpec", "ParamSpecArgs", "ParamSpecKwargs",
    "Pattern", "Protocol", "Required", "Reversible", "Self", "Sequence", "Set", "Sized",
    "SupportsAbs", "SupportsBytes", "SupportsComplex", "SupportsFloat", "SupportsIndex",
    "SupportsInt", "SupportsRound", "TYPE_CHECKING", "Text", "TextIO", "Tuple", "Type",
    "TypeAlias", "TypeAliasType", "TypeGuard", "TypeVar", "TypeVarTuple", "TypedDict", "Union",
    "Unpack", "ValuesView", "assert_never", "assert_type", "cast", "clear_overloads",
    "dataclass_transform", "final", "get_args", "get_origin", "get_overloads",
    "get_type_hints", "is_typeddict", "no_type_check", "no_type_check_decorator", "overload",
    "override", "reveal_type", "runtime_checkable",
})
# collections.abc.__all__ on Python 3.12.3 (3.13's is the same).
ABC_312 = frozenset({
    "AsyncGenerator", "AsyncIterable", "AsyncIterator", "Awaitable", "Buffer", "ByteString",
    "Callable", "Collection", "Container", "Coroutine", "Generator", "Hashable", "ItemsView",
    "Iterable", "Iterator", "KeysView", "Mapping", "MappingView", "MutableMapping",
    "MutableSequence", "MutableSet", "Reversible", "Sequence", "Set", "Sized", "ValuesView",
})
NAMES_312 = {"typing": TYPING_312, "collections.abc": ABC_312}
# Builtins of 3.13 and later that 3.12 lacks.
BUILTINS_NOT_312 = frozenset({"PythonFinalizationError"})
BUILTIN_NAMES = frozenset(dir(builtins))
# A class with one of these bases (by name, plain or as an attribute) reads
# its body's annotations: its AnnAssigns are fields or members.
SPECIAL_BASES = frozenset({"NamedTuple", "TypedDict", "Enum", "IntEnum", "StrEnum", "Flag",
                           "IntFlag", "Protocol", "Generic"})
# The builtins a TypeAlias value may subscript (PEP 585, Python 3.9).
GENERIC_BUILTINS = frozenset({"dict", "list", "tuple", "set", "frozenset", "type"})


class Refused(Exception):
    """The repository or the arguments name nothing this tool can check."""


# ------------------------------------------------------------------ reading

def _bound(a: ast.alias, plain_import: bool) -> str:
    """The name an import's alias binds."""
    if a.asname:
        return a.asname
    return a.name.split(".")[0] if plain_import else a.name


def _named(e: Optional[ast.AST], name: str) -> bool:
    """E spells NAME, plain or as an attribute (`typing.NAME`)."""
    return (isinstance(e, ast.Name) and e.id == name) or (
        isinstance(e, ast.Attribute) and e.attr == name)


def is_future(st: ast.stmt) -> bool:
    return (isinstance(st, ast.ImportFrom) and st.module == "__future__" and st.level == 0
            and any(a.name == "annotations" for a in st.names))


def exact_future(st: ast.stmt) -> bool:
    """`from __future__ import annotations`, alone in its statement."""
    return (is_future(st) and len(st.names) == 1 and st.names[0].asname is None)


def has_future(tree: ast.Module) -> bool:
    return any(is_future(st) for st in tree.body)


def doc_index(tree: ast.Module) -> int:
    """Where the future import goes: after the docstring, if there is one."""
    b = tree.body
    return 1 if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
        and isinstance(b[0].value.value, str) else 0


def is_typing_import(st: ast.stmt) -> bool:
    if isinstance(st, ast.Import):
        return all(a.name in TYPING_MODULES for a in st.names)
    return isinstance(st, ast.ImportFrom) and st.level == 0 and st.module in TYPING_MODULES


def typing_sources(st: ast.stmt) -> List[Tuple[str, Tuple[str, ...]]]:
    """(bound name, what it binds) for a typing import: ("module", M) or
    ("attr", M, NAME); a star import is ("star", M)."""
    if isinstance(st, ast.Import):
        return [(_bound(a, True), ("module", a.name if a.asname else a.name.split(".")[0]))
                for a in st.names]
    assert isinstance(st, ast.ImportFrom)
    return [("*", ("star", st.module or "")) if a.name == "*" else
            (_bound(a, False), ("attr", st.module or "", a.name)) for a in st.names]


def is_alias(st: ast.stmt) -> bool:
    """`X: TypeAlias = value` (the statement, wherever it stands)."""
    return (isinstance(st, ast.AnnAssign) and st.value is not None and st.simple == 1
            and isinstance(st.target, ast.Name) and _named(st.annotation, "TypeAlias"))


def scope_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """NODE and what below it runs in NODE's scope: not a nested def's,
    class's or lambda's body (their decorators, defaults and bases are the
    scope's), nor a comprehension's target."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            todo += n.args.defaults + [d for d in n.args.kw_defaults if d is not None]
            if not isinstance(n, ast.Lambda):
                todo += n.decorator_list
        elif isinstance(n, ast.ClassDef):
            todo += n.decorator_list + n.bases + [k.value for k in n.keywords]
        elif isinstance(n, ast.comprehension):
            todo += [n.iter] + n.ifs
        else:
            todo += list(ast.iter_child_nodes(n))


def _binding(n: ast.AST) -> List[Tuple[str, str]]:
    """(name, how) for each name node N binds in its scope."""
    if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
        return [(n.id, "an assignment")]
    if isinstance(n, ast.arg):
        return [(n.arg, "a parameter")]
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [(n.name, "a def")]
    if isinstance(n, ast.ClassDef):
        return [(n.name, "a class")]
    if isinstance(n, ast.Import):
        return [(_bound(a, True), f"an import of {a.name}") for a in n.names]
    if isinstance(n, ast.ImportFrom):
        src = "." * n.level + (n.module or "")
        return [(_bound(a, False), f"an import from {src}") for a in n.names]
    if isinstance(n, ast.ExceptHandler) and n.name:
        return [(n.name, "an except clause")]
    if isinstance(n, (ast.Global, ast.Nonlocal)):
        return [(x, "a global statement") for x in n.names]
    if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
        return [(n.name, "a match pattern")]
    if isinstance(n, ast.MatchMapping) and n.rest:
        return [(n.rest, "a match pattern")]
    if type(n).__name__ in ("TypeVar", "ParamSpec", "TypeVarTuple"):
        return [(getattr(n, "name", "?"), "a type parameter")]
    return []


class Facts:
    """What one side's module binds, read from its source: which names are
    typing's TYPE_CHECKING and typing's module, whether `cast` is typing's,
    the statements that run at module level, what they bind, and the
    imports from typing and collections.abc among them."""

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.every: Dict[str, List[Tuple[ast.AST, int, str]]] = {}
        for n in ast.walk(tree):
            for name, how in _binding(n):
                self.every.setdefault(name, []).append((n, getattr(n, "lineno", 0), how))
        self._direct = {id(st) for st in tree.body}
        star = self.every.get("*")
        attr_stores = {n.attr for n in ast.walk(tree)
                       if isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Load)}

        def only_by(name: str, module: str, attr: Optional[str]) -> bool:
            got = self.every.get(name, [])
            return bool(got) and not star and all(self._imports(n, name, module, attr)
                                                  for n, _l, _h in got)
        self.typing_mods = {_bound(a, True) for st in tree.body if isinstance(st, ast.Import)
                            for a in st.names if a.name == "typing"}
        self.typing_mods = {m for m in self.typing_mods if only_by(m, "typing", None)}
        self.tc_names = {_bound(a, False) for st in tree.body if isinstance(st, ast.ImportFrom)
                         for a in st.names if a.name == "TYPE_CHECKING"}
        self.tc_names = {t for t in self.tc_names if only_by(t, "typing", "TYPE_CHECKING")}
        self.tc_attr = "TYPE_CHECKING" not in attr_stores
        self.cast_ok = only_by("cast", "typing", "cast")
        self.cast_attr = "cast" not in attr_stores
        self.cast_why = self._cast_why(star) if self.cast_attr else \
            "an attribute named cast is assigned in the module"
        # The module-level TypeAlias names compared as assignments, not
        # stripped (the Pair's: BASE's, and HEAD's where BASE binds the name).
        self.kept_aliases: Set[str] = set()
        self.tops: List[ast.stmt] = []
        self.tc_body: List[ast.stmt] = []
        self._split(tree.body)
        self._module_level()

    def _imports(self, n: ast.AST, name: str, module: str, attr: Optional[str]) -> bool:
        """N, a statement of the module's body, binds NAME by `import MODULE`
        (ATTR None) or `from MODULE import ATTR`."""
        if id(n) not in self._direct:
            return False
        if attr is None:
            return isinstance(n, ast.Import) and any(
                a.name == module and _bound(a, True) == name for a in n.names)
        return isinstance(n, ast.ImportFrom) and n.module == module and n.level == 0 and any(
            a.name == attr and _bound(a, False) == name for a in n.names)

    def _cast_why(self, star) -> str:
        if star:
            return f"a star import (line {star[0][1]}) may bind cast"
        for n, line, how in self.every.get("cast", []):
            if not self._imports(n, "cast", "typing", "cast"):
                return (f"cast is bound by {how} (line {line}), not only by `from typing "
                        f"import cast` at module level")
        return "cast is not imported from typing at module level"

    def is_tc(self, test: ast.expr) -> bool:
        """TEST is typing's TYPE_CHECKING, through a name nothing else binds."""
        if isinstance(test, ast.Name):
            return test.id in self.tc_names
        return (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING" and self.tc_attr
                and isinstance(test.value, ast.Name) and test.value.id in self.typing_mods)

    def names_cast(self, f: ast.expr) -> bool:
        """F is typing's cast: `cast` bound by `from typing import cast`
        alone, or `M.cast` for M bound by `import typing` alone."""
        return (isinstance(f, ast.Name) and f.id == "cast" and self.cast_ok) or (
            isinstance(f, ast.Attribute) and f.attr == "cast" and self.cast_attr
            and isinstance(f.value, ast.Name) and f.value.id in self.typing_mods)

    def is_cast(self, call: ast.Call) -> bool:
        """typing's cast, called as `cast("T", v)`."""
        return (self.names_cast(call.func) and len(call.args) == 2 and not call.keywords
                and not any(isinstance(a, ast.Starred) for a in call.args)
                and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str))

    def _split(self, body: Sequence[ast.stmt]) -> None:
        for st in body:
            if isinstance(st, ast.If) and self.is_tc(st.test):
                self.tc_body += st.body
                self._split(st.orelse)
            else:
                self.tops.append(st)

    def _module_level(self) -> None:
        """What each statement that runs at module level binds; the typing
        imports and TypeAlias statements among them."""
        self.binds: Dict[str, List[Tuple[int, str]]] = {}
        self.top_binds: List[Set[str]] = []
        self.typing_bound: Dict[str, Tuple[str, ...]] = {}
        self.typing_line: Dict[str, int] = {}
        self.aliases: List[Tuple[str, int, ast.expr, int]] = []
        self.imports: Dict[str, List[Tuple[int, str]]] = {}
        for i, st in enumerate(self.tops):
            names: Set[str] = set()
            if is_typing_import(st):
                self._plain_imports(st, i)
                for name, src in typing_sources(st):
                    self.typing_bound[name] = src
                    self.typing_line[name] = st.lineno
                    self.binds.setdefault(name, []).append((st.lineno, "a typing import"))
                    names.add(name)
            elif is_alias(st):
                assert isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                self.aliases.append((st.target.id, st.lineno, st.value, i))
                names.add(st.target.id)
            else:
                for n in scope_nodes(st):
                    for name, how in _binding(n):
                        self.binds.setdefault(name, []).append((getattr(n, "lineno", 0), how))
                        names.add(name)
                    self._plain_imports(n, i)
            self.top_binds.append(names)
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Global):
                for name in n.names:
                    self.binds.setdefault(name, []).append((n.lineno, "a global statement"))

    def _plain_imports(self, n: ast.AST, index: int) -> None:
        """The module each name of a plain import holds (`import a.b` binds a)."""
        if isinstance(n, ast.Import):
            for a in n.names:
                self.imports.setdefault(_bound(a, True), []).append(
                    (index, a.name if a.asname else a.name.split(".")[0]))

    def bound_before(self, name: str, index: int) -> bool:
        return any(name in s for s in self.top_binds[:index])

    def module_before(self, name: str, index: int) -> Optional[str]:
        """The module NAME holds at the INDEXth statement, when only a plain
        import binds it before there (and nothing else at module level)."""
        got = [m for i, m in self.imports.get(name, []) if i < index]
        others = [b for b in self.binds.get(name, [])
                  if not b[1].startswith("an import of ") and b[1] != "a typing import"]
        if not got or others or len(set(got)) != 1:
            return None
        return got[0]


# ------------------------------------------------------------ normalizing

def class_kind(c: ast.ClassDef) -> str:
    """`plain` (no decorator, no metaclass, no base that reads annotations)
    or `special`."""
    if c.decorator_list or any(k.arg == "metaclass" for k in c.keywords):
        return "special"
    for b in c.bases:
        while isinstance(b, ast.Subscript):
            b = b.value
        if isinstance(b, ast.Name):
            name = b.id
        elif isinstance(b, ast.Attribute):
            name = b.attr
        else:
            return "special"        # a base computed by a call: it may read them
        if name in SPECIAL_BASES:
            return "special"
    return "plain"


def _annassign(st: ast.AnnAssign, scope: str, top: bool, kept: Set[str]) -> List[ast.stmt]:
    if scope == "special":
        st.annotation = ast.Constant(MARKER)
        return [st]
    if scope == "module" and top and is_alias(st) and isinstance(st.target, ast.Name) \
            and st.target.id not in kept:
        return []               # a TypeAlias HEAD adds: stages purity and bindings
    if st.value is not None:
        return [ast.copy_location(ast.Assign(targets=[st.target], value=st.value,
                                             type_comment=None), st)]
    if isinstance(st.target, ast.Name) and (scope != "function" or st.simple):
        # A bare `x: T` binds nothing. In a function it makes x local: the
        # functions' code, compiled with it, holds that (LOAD_FAST against
        # LOAD_GLOBAL or LOAD_DEREF).
        return []
    st.annotation = ast.Constant(MARKER)
    return [st]                 # kept: `o.x: T` evaluates o, `(x): T` stays as written


def _body(body: List[ast.stmt], scope: str, facts: Facts, full: bool, top: bool) -> List[ast.stmt]:
    out: List[ast.stmt] = []
    for st in body:
        if isinstance(st, ast.If) and facts.is_tc(st.test) and (full or scope != "function"):
            out += _body(st.orelse, scope, facts, full, top)
        else:
            out += _stmt(st, scope, facts, full, top)
    return out


def _stmt(st: ast.stmt, scope: str, facts: Facts, full: bool, top: bool) -> List[ast.stmt]:
    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
        st.body = _body(st.body, "function", facts, full, False)
        return [st]
    if isinstance(st, ast.ClassDef):
        st.body = _body(st.body, class_kind(st), facts, full, False)
        return [st]
    if full and isinstance(st, ast.AnnAssign):
        return _annassign(st, scope, top, facts.kept_aliases)
    if full and top and scope == "module" and is_typing_import(st):
        return []
    for field in ("body", "orelse", "finalbody"):
        sub = getattr(st, field, None)
        if isinstance(sub, list):
            setattr(st, field, _body(sub, scope, facts, full, False))
    for part in getattr(st, "handlers", None) or getattr(st, "cases", None) or []:
        part.body = _body(part.body, scope, facts, full, False)
    return [st]


class _Casts(ast.NodeTransformer):
    def __init__(self, facts: Facts):
        self.facts = facts

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        return node.args[1] if self.facts.is_cast(node) else node


def _finish(tree: ast.Module, facts: Facts) -> ast.Module:
    """Signature annotations and type comments off, casts stripped, an
    emptied body given a `pass`."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n.returns = None
            a = n.args
            for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if x is not None:
                    x.annotation = None
                    x.type_comment = None
        if hasattr(n, "type_comment"):
            n.type_comment = None
    tree = _Casts(facts).visit(tree)
    for n in ast.walk(tree):
        if not isinstance(n, ast.Module) and isinstance(getattr(n, "body", None), list) \
                and not n.body:
            n.body = [ast.Pass()]
    return ast.fix_missing_locations(tree)


def normalize(tree: ast.Module, facts: Facts) -> ast.Module:
    """The typing mode's normalization (stage ast): a new tree."""
    t = copy.deepcopy(tree)
    t.body = _body(t.body, "module", facts, True, True)
    return _finish(t, facts)


def for_functions(tree: ast.Module, facts: Facts) -> ast.Module:
    """What stage bytecode compiles for the functions' code: signature
    annotations and casts stripped, local AnnAssigns kept (TYPE_CHECKING
    blocks outside functions go too: nothing in them runs)."""
    t = copy.deepcopy(tree)
    t.body = _body(t.body, "module", facts, False, True)
    return _finish(t, facts)


def without_future(tree: ast.Module) -> ast.Module:
    t = copy.deepcopy(tree)
    t.body = [st for st in t.body if not exact_future(st)]
    return t


def dump(node: object) -> str:
    if isinstance(node, list):
        return "[" + ", ".join(dump(x) for x in node) + "]"
    return ast.dump(node) if isinstance(node, ast.AST) else repr(node)


def _snip(node: Optional[ast.AST]) -> str:
    try:
        text = ast.unparse(node).split("\n")[0] if node is not None else ""
    except (AttributeError, ValueError, TypeError):
        text = type(node).__name__
    return text if len(text) <= 60 else text[:57] + "..."


def _blocks(fx: object, fy: object) -> bool:
    return any(isinstance(v, (ast.stmt, ast.excepthandler, ast.match_case))
               for v in (fx if isinstance(fx, list) else []) + (fy if isinstance(fy, list) else []))


def first_difference(x: ast.AST, y: ast.AST) -> Tuple[Optional[ast.AST], Optional[ast.AST]]:
    """The innermost statements (BASE's, HEAD's) of X and Y that differ."""
    for f in x._fields:
        fx, fy = getattr(x, f, None), getattr(y, f, None)
        if not _blocks(fx, fy) and dump(fx) != dump(fy):
            return x, y
    for f in x._fields:
        fx, fy = getattr(x, f, None), getattr(y, f, None)
        if _blocks(fx, fy) and dump(fx) != dump(fy):
            assert isinstance(fx, list) and isinstance(fy, list)
            return _list_difference(fx, fy)
    return x, y


def _list_difference(xs: list, ys: list) -> Tuple[Optional[ast.AST], Optional[ast.AST]]:
    """Two statement lists that differ: a statement HEAD adds (None, it),
    one it drops (it, None), or the first pair that differs, descended."""
    n, m, i = len(xs), len(ys), 0
    while i < min(n, m) and dump(xs[i]) == dump(ys[i]):
        i += 1
    if m > n and dump(ys[i + m - n:]) == dump(xs[i:]):
        return None, ys[i]
    if n > m and dump(xs[i + n - m:]) == dump(ys[i:]):
        return xs[i], None
    a, b = (xs[i] if i < n else None), (ys[i] if i < m else None)
    if a is not None and b is not None and type(a) is type(b):
        return first_difference(a, b)
    return a, b


def describe(rel: str, base: ast.Module, head: ast.Module) -> str:
    """`file:line: what` for the first difference between two trees."""
    x, y = first_difference(base, head)
    if isinstance(x, ast.Module) or isinstance(y, ast.Module):
        return f"{rel}:1: the module differs from BASE's"
    if y is None:
        return f"{rel}:{getattr(x, 'lineno', 1)}: HEAD drops `{_snip(x)}` (that line of BASE's)"
    line = getattr(y, "lineno", None) or getattr(getattr(y, "pattern", None), "lineno", 1)
    if x is None:
        return f"{rel}:{line}: HEAD adds `{_snip(y)}`"
    return f"{rel}:{line}: `{_snip(y)}` is not BASE's `{_snip(x)}`"


def _own_scope(body: Sequence[ast.stmt], whole_tc: bool) -> Iterator[ast.stmt]:
    """BODY's statements and those of its if, for, while, with, try and
    match blocks: not a def's or a class's, nor (WHOLE_TC) a TYPE_CHECKING
    block's, which is yielded whole."""
    for st in body:
        yield st
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or (
                whole_tc and _tc_named(st)):
            continue
        for field in ("body", "orelse", "finalbody"):
            sub = getattr(st, field, None)
            if isinstance(sub, list):
                yield from _own_scope(sub, whole_tc)
        for part in getattr(st, "handlers", None) or getattr(st, "cases", None) or []:
            yield from _own_scope(part.body, whole_tc)


def local_classes(tree: ast.Module) -> Dict[Tuple[str, int], List[ast.stmt]]:
    """Each class defined inside a function, at any depth, by (qualname,
    order among equal ones): the AnnAssigns and TYPE_CHECKING blocks of its
    body's own scope."""
    out: Dict[Tuple[str, int], List[ast.stmt]] = {}
    seen: Dict[str, int] = {}

    def walk(body: Sequence[ast.stmt], prefix: str, in_function: bool) -> None:
        for st in _own_scope(body, False):
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(st.body, f"{prefix}{st.name}.<locals>.", True)
            elif isinstance(st, ast.ClassDef):
                q = prefix + st.name
                i = seen.get(q, 0)
                seen[q] = i + 1
                if in_function:
                    out[(q, i)] = [s for s in _own_scope(st.body, True)
                                   if isinstance(s, ast.AnnAssign) or _tc_named(s)]
                walk(st.body, q + ".", in_function)
    walk(tree.body, "", False)
    return out


def local_class_problems(rel: str, base: ast.Module, head: ast.Module) -> List[str]:
    """Typing mode: an AnnAssign or a TYPE_CHECKING block HEAD adds, drops
    or changes in a class inside a function. A name it binds is the class
    body's own: the body reads it with LOAD_NAME (the globals), no longer
    the function's variable. The ast stage drops a plain class's bare `x: T`
    and every TYPE_CHECKING block, and the bytecode stage compiles class
    bodies from that, so neither would see it."""
    b, h = local_classes(base), local_classes(head)
    out: List[str] = []
    for key in sorted(set(b) | set(h)):
        xs, ys = b.get(key, []), h.get(key, [])
        if dump(xs) == dump(ys):
            continue
        x, y = _list_difference(xs, ys)
        if y is None:
            node, what = x, f"HEAD drops `{_snip(x)}`"
        elif x is None:
            node, what = y, f"HEAD adds `{_snip(y)}`"
        else:
            node, what = y, f"`{_snip(y)}` is not BASE's `{_snip(x)}`"
        out.append(f"{rel}:{getattr(node, 'lineno', 1)}: {what} in {key[0]}, a class inside a "
                   "function: an annotation or a TYPE_CHECKING import there makes the name the "
                   "class body's own, read from the globals, not the function's variable "
                   "(the bytecode stage sees class bodies only as normalized)")
    return out


# ------------------------------------------------------------- the module

class Pair:
    """One changed module: BASE's and HEAD's source, parsed; its mode."""

    def __init__(self, rel: str, base: bytes, head: bytes):
        self.rel = rel
        self.mode: Optional[str] = None
        self.problems: Dict[str, List[str]] = {s: [] for s in STAGES}
        self.compared = 0
        self.new_aliases: List[Tuple[str, int, ast.expr, int]] = []
        try:
            self.base = ast.parse(base, rel)
            self.head = ast.parse(head, rel)
        except (SyntaxError, ValueError) as e:
            self.problems["ast"].append(f"{rel}:{getattr(e, 'lineno', 1) or 1}: does not parse: {e}")
            return
        self.base_facts, self.head_facts = Facts(self.base), Facts(self.head)
        # BASE's TypeAlias statements, and HEAD's for a name BASE binds at
        # module level, are compared (as assignments); only the rest, the
        # ones HEAD adds, are stripped, and proved harmless instead.
        B, H = self.base_facts, self.head_facts
        kept = {a[0] for a in B.aliases} | {a[0] for a in H.aliases if a[0] in B.binds}
        B.kept_aliases = H.kept_aliases = kept
        self.new_aliases = [a for a in H.aliases if a[0] not in kept]
        self._mode()

    @property
    def module(self) -> str:
        parts = self.rel[len("src/"):-len(".py")].split("/")
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    def _mode(self) -> None:
        b, h, rel = has_future(self.base), has_future(self.head), self.rel
        refuse = self.problems["ast"].append
        if b and h:
            self.mode = "typing"
        elif b:
            refuse(f"{rel}:1: HEAD removes `from __future__ import annotations`")
        elif not h:
            if dump(self.base) == dump(self.head):
                self.mode = "unchanged"
            else:
                refuse(describe(rel, self.base, self.head) + "; BASE lacks `from __future__ "
                       "import annotations`: add the future import first, in its own commit")
        else:
            i, hb = doc_index(self.base), self.head.body
            if len(hb) == len(self.base.body) + 1 and i < len(hb) and exact_future(hb[i]) and \
                    dump(hb[:i] + hb[i + 1:]) == dump(self.base.body):
                self.mode = "future"
            elif dump(without_future(self.head)) == dump(self.base):
                refuse(f"{rel}:1: the future import goes directly after the module docstring, "
                       "alone in its statement")
            else:
                refuse(describe(rel, self.base, without_future(self.head)) + "; HEAD adds the "
                       "future import with other changes: add the future import first, in its "
                       "own commit")

    def run_static(self) -> None:
        if self.mode in ("typing", "future"):
            if self.mode == "typing":
                self.base_norm = normalize(self.base, self.base_facts)
                self.head_norm = normalize(self.head, self.head_facts)
                if dump(self.base_norm) != dump(self.head_norm):
                    self.problems["ast"].append(describe(self.rel, self.base_norm, self.head_norm))
                self.problems["ast"] += local_class_problems(self.rel, self.base, self.head)
            self.problems["bytecode"] += self._bytecode()
            if self.mode == "typing":
                self.problems["purity"] += purity_problems(self.rel, self.head_facts,
                                                           self.new_aliases)
                self.problems["bindings"] += self._bindings()
            else:
                self.problems["purity"] += eager_problems(self.rel, self.base)
                self.problems["bindings"] += self._annotations_name()

    # ------------------------------------------------------- bytecode

    def _bytecode(self) -> List[str]:
        mask = FUTURE_FLAG if self.mode == "future" else 0
        head = without_future(self.head) if self.mode == "future" else self.head
        out: List[str] = []
        try:
            fa = (_compile(for_functions(self.base, self.base_facts), self.rel),
                  _compile(for_functions(self.head, self.head_facts), self.rel))
            fb = (_compile_source(ast.unparse(normalize(self.base, self.base_facts)), self.rel),
                  _compile_source(ast.unparse(normalize(head, self.head_facts)), self.rel))
        except (SyntaxError, ValueError, TypeError) as e:
            return [f"{self.rel}:1: does not compile once normalized: {e}"]
        for codes, functions in ((fa, True), (fb, False)):
            bc, hc = (code_objects(c, functions) for c in codes)
            for key in sorted(set(bc) - set(hc)):
                out.append(f"{self.rel}:{bc[key].co_firstlineno}: HEAD lacks BASE's code {key[0]}")
            for key in sorted(set(hc) - set(bc)):
                out.append(f"{self.rel}:{hc[key].co_firstlineno}: HEAD adds code {key[0]}")
            for key in sorted(set(bc) & set(hc)):
                self.compared += 1
                diff = code_difference(bc[key], hc[key], mask)
                if diff:
                    out.append(f"{self.rel}:{hc[key].co_firstlineno}: {key[0]}: {diff}")
        return out

    # ------------------------------------------------------- bindings

    def _annotations_name(self) -> List[str]:
        """Future mode: the import binds `annotations` in the module."""
        used = [n for n in ast.walk(self.base) if isinstance(n, ast.Name) and n.id == "annotations"]
        used += [n for n, _l, _h in self.base_facts.every.get("annotations", [])
                 if not isinstance(n, ast.arg)]
        if used:
            return [f"{self.rel}:{getattr(used[0], 'lineno', 1)}: BASE uses the name `annotations`, "
                    "which the future import binds"]
        return []

    def _bindings(self) -> List[str]:
        B, H, rel = self.base_facts, self.head_facts, self.rel
        out: List[str] = []
        for name, src in sorted(B.typing_bound.items()):
            if H.typing_bound.get(name) != src:
                out.append(f"{rel}:{B.typing_line[name]}: HEAD no longer binds {name} from "
                           f"{src[1]} at module level: code that reads it would fail")
        reads = runtime_reads(self.base_norm)
        for name, src in sorted(H.typing_bound.items()):
            if name in B.typing_bound:
                continue
            line = H.typing_line[name]
            out += [f"{rel}:{line}: {p}" for p in new_name_problems(name, src, B, H, reads)]
        out += self._extensions()
        base_casts = [dump(n) for n in _casts_kept(self.base, B)]
        for n in _casts_kept(self.head, H):
            if dump(n) in base_casts:
                base_casts.remove(dump(n))
            elif not H.names_cast(n.func):
                out.append(f"{rel}:{n.lineno}: no cast call is stripped: {H.cast_why}")
                break
        out += self._alias_names(reads | runtime_reads(self.head_norm))
        seen = {dump(n) for n in ast.walk(self.base) if _tc_named(n) and not B.is_tc(n.test)}
        for n in ast.walk(self.head):
            if _tc_named(n) and not H.is_tc(n.test) and dump(n) not in seen:
                out.append(f"{rel}:{n.lineno}: this `if` reads a TYPE_CHECKING that is not "
                           "typing's alone (bound by `from typing import TYPE_CHECKING` and "
                           "nothing else): its block runs")
        return out

    def _extensions(self) -> List[str]:
        def found(tree: ast.Module) -> List[ast.stmt]:
            return [n for n in ast.walk(tree) if (isinstance(n, ast.ImportFrom) and (
                n.module or "").split(".")[0] == "typing_extensions") or (isinstance(
                    n, ast.Import) and any(a.name.split(".")[0] == "typing_extensions"
                                           for a in n.names))]
        base = [dump(n) for n in found(self.base)]
        out = []
        for n in found(self.head):
            if dump(n) in base:
                base.remove(dump(n))
            else:
                out.append(f"{self.rel}:{n.lineno}: imports typing_extensions, which is not a "
                           "dependency of taxjson")
        return out

    def _alias_names(self, reads: Set[str]) -> List[str]:
        B, H, rel = self.base_facts, self.head_facts, self.rel
        out: List[str] = []
        names = [a[0] for a in H.aliases]
        for name, line, _value, _i in self.new_aliases:
            why = None
            if names.count(name) > 1:
                why = "is the name of another TypeAlias"
            elif name in BUILTIN_NAMES:
                why = "is a builtin"
            elif H.binds.get(name):
                why = f"is bound by {H.binds[name][0][1]} (line {H.binds[name][0][0]})"
            elif B.binds.get(name) or name in B.typing_bound:
                why = f"is bound in BASE (line {(B.binds.get(name) or [(1, '')])[0][0]})"
            elif name in reads:
                why = "is read by the module's code at run time"
            if why:
                out.append(f"{rel}:{line}: the TypeAlias {name} {why}")
        return out


def _casts_kept(tree: ast.Module, facts: Facts) -> List[ast.Call]:
    """The calls of `cast` or `M.cast` (M a name `import typing` binds
    anywhere) that are not stripped."""
    mods = {_bound(a, True) for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names if a.name == "typing"}
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and not facts.is_cast(n) and (
        (isinstance(n.func, ast.Name) and n.func.id == "cast") or (
            isinstance(n.func, ast.Attribute) and n.func.attr == "cast"
            and isinstance(n.func.value, ast.Name) and n.func.value.id in mods))]


def _tc_named(n: ast.AST) -> bool:
    return isinstance(n, ast.If) and _named(n.test, "TYPE_CHECKING")


def runtime_reads(norm: ast.Module) -> Set[str]:
    """The names a side's normalized code reads or binds when it runs (its
    new TypeAlias statements are gone)."""
    return {n.id for n in ast.walk(norm) if isinstance(n, ast.Name)}


def new_name_problems(name: str, src: Tuple[str, ...], B: Facts, H: Facts,
                      reads: Set[str]) -> List[str]:
    """Why a name HEAD newly binds from typing or collections.abc may change
    what runs."""
    if src[0] == "star":
        return [f"a star import from {src[1]}: name each import"]
    if src[0] == "attr" and src[2] not in NAMES_312[src[1]]:
        return [f"{src[1]}.{src[2]} is not in Python 3.12's {src[1]} (the project's floor): "
                f"an ImportError there"]
    if name in BUILTIN_NAMES:
        return [f"{name} would shadow the builtin"]
    other = B.binds.get(name, []) + [b for b in H.binds.get(name, []) if b[1] != "a typing import"]
    if other:
        line, how = other[0]
        return [f"{name} would shadow {how} (line {line}{' of BASE' if B.binds.get(name) else ''})"]
    if name in reads:
        return [f"BASE's code reads {name}: binding it changes what that read finds"]
    return []


def _compile(tree: ast.Module, rel: str) -> types.CodeType:
    return compile(tree, rel, "exec", dont_inherit=True, optimize=0)


def _compile_source(text: str, rel: str) -> types.CodeType:
    return compile(text, rel, "exec", dont_inherit=True, optimize=0)


def code_objects(code: types.CodeType, functions: bool) -> Dict[Tuple[str, int], types.CodeType]:
    """The code objects below CODE (and CODE itself, for class bodies and the
    module), by (qualname, order among equal qualnames): the functions' or
    the others'."""
    seen: Dict[str, int] = {}
    out: Dict[Tuple[str, int], types.CodeType] = {}
    if not functions:
        out[("<module>", 0)] = code

    def walk(c: types.CodeType) -> None:
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                i = seen.get(k.co_qualname, 0)
                seen[k.co_qualname] = i + 1
                if bool(k.co_flags & CO_OPTIMIZED) == functions:
                    out[(k.co_qualname, i)] = k
                walk(k)
    walk(code)
    return out


def _const(k: object) -> object:
    """A constant as compared: its type with its value (1, 1.0 and True
    differ), a code object by its qualname (compared on its own)."""
    if isinstance(k, types.CodeType):
        return ("code", k.co_qualname)
    if isinstance(k, tuple):
        return ("tuple", tuple(_const(x) for x in k))
    if isinstance(k, frozenset):
        return ("frozenset", tuple(sorted(repr(_const(x)) for x in k)))
    return (type(k).__name__, repr(k))


# Compared in this order, the names first: the first that differs is named.
CODE_FIELDS = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_flags",
               "co_varnames", "co_cellvars", "co_freevars", "co_names", "co_consts", "co_code",
               "co_exceptiontable")


def code_difference(a: types.CodeType, b: types.CodeType, mask: int) -> Optional[str]:
    for f in CODE_FIELDS:
        x, y = getattr(a, f), getattr(b, f)
        if f == "co_flags":
            x, y = x & ~mask, y & ~mask
        elif f == "co_consts":
            x, y = tuple(_const(k) for k in x), tuple(_const(k) for k in y)
        if x != y:
            if f in ("co_code", "co_exceptiontable"):
                return f"{f} differs from BASE's"
            return f"{f} is {_short(y)}, BASE's {_short(x)}"
    return None


def _short(v: object, n: int = 80) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n - 3] + "..."


# ----------------------------------------------------------------- purity

def purity_problems(rel: str, facts: Facts, aliases: List[Tuple[str, int, ast.expr, int]]) -> List[str]:
    """The TypeAlias values HEAD adds (ALIASES, FACTS's): evaluated at import."""
    out: List[str] = []
    for name, line, value, index in aliases:
        for why in _type_expr(value, index, facts, False):
            out.append(f"{rel}:{line}: the TypeAlias {name}: {why}")
    return out


_TYPE_NODES = (ast.Name, ast.Attribute, ast.Subscript, ast.Constant, ast.Tuple, ast.List,
               ast.BinOp, ast.BitOr, ast.Load)


def eager_problems(rel: str, tree: ast.Module) -> List[str]:
    """Future mode: BASE evaluates its annotations (at import, or when a
    nested def runs) and HEAD will not. Each must run no code: names,
    attributes, subscripts, constants, tuples, lists and `|` only."""
    out: List[str] = []
    for n in ast.walk(tree):
        anns: List[Optional[ast.expr]] = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            anns = [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
                    if x is not None] + [n.returns]
        elif isinstance(n, ast.AnnAssign):
            anns = [n.annotation]
        for ann in anns:
            bad = [x for x in ast.walk(ann) if not isinstance(x, _TYPE_NODES) or (
                isinstance(x, ast.BinOp) and not isinstance(x.op, ast.BitOr))] if ann else []
            if bad:
                out.append(f"{rel}:{ann.lineno}: BASE evaluates the annotation `{_snip(ann)}` "
                           f"(a {type(bad[0]).__name__} in it), which the future import stops")
    return out


def _dotted(e: ast.expr) -> Optional[List[str]]:
    parts: List[str] = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if not isinstance(e, ast.Name):
        return None
    return [e.id] + parts[::-1]


def _resolve(e: ast.expr, index: int, facts: Facts) -> Tuple[Optional[str], str]:
    """What an attribute chain names at the INDEXth statement: the dotted
    name from a module a plain import bound before, or (None, why)."""
    parts = _dotted(e)
    if parts is None:
        return None, f"`{_snip(e)}` is no name"
    module = facts.module_before(parts[0], index)
    if module is None:
        return None, f"{parts[0]} is not a module an import binds before the alias"
    full = ".".join([module] + parts[1:])
    for mod in TYPING_MODULES:
        if full.startswith(mod + ".") and full[len(mod) + 1:] not in NAMES_312[mod]:
            return None, f"{full} is not in Python 3.12's {mod}"
    return full, ""


def _subscriptable(v: ast.expr, index: int, facts: Facts) -> Optional[str]:
    """Why V, subscripted in a TypeAlias value, may run code: None when it
    is a typing or collections.abc name or a builtin generic."""
    if isinstance(v, ast.Name):
        src = facts.typing_bound.get(v.id)
        if src and src[0] == "attr" and facts.bound_before(v.id, index):
            return None
        if v.id in GENERIC_BUILTINS and v.id not in facts.binds and v.id not in facts.typing_bound:
            return None
        return f"it subscripts {v.id}, not a typing or collections.abc name bound before it"
    if isinstance(v, ast.Attribute):
        full, why = _resolve(v, index, facts)
        if full is None:
            return why
        if any(full.startswith(m + ".") for m in TYPING_MODULES):
            return None
        return f"it subscripts {full}, not a typing or collections.abc name"
    return f"it subscripts `{_snip(v)}`"


def _type_expr(e: ast.expr, index: int, facts: Facts, in_slice: bool) -> List[str]:
    """Why evaluating E (a TypeAlias value, at import) may run code or fail
    on Python 3.12."""
    if isinstance(e, ast.Constant):
        if not in_slice:            # standing alone: a forward reference or None is a type
            ok = e.value is None or isinstance(e.value, str)
            return [] if ok else [f"the constant {e.value!r} is no type: standing alone, only a "
                                  "string (a forward reference) or None is"]
        ok = e.value is None or e.value is Ellipsis or isinstance(e.value, (str, bytes, int))
        return [] if ok else [f"the constant {e.value!r}"]
    if isinstance(e, ast.Name):
        if facts.bound_before(e.id, index) or (e.id in BUILTIN_NAMES and e.id not in BUILTINS_NOT_312):
            return []
        return [f"{e.id} is not bound before it outside TYPE_CHECKING (nor a builtin of 3.12)"]
    if isinstance(e, ast.Attribute):
        full, why = _resolve(e, index, facts)
        return [] if full else [why]
    if isinstance(e, ast.Subscript):
        why = _subscriptable(e.value, index, facts)
        return ([why] if why else []) + _type_expr(e.slice, index, facts, True)
    if isinstance(e, ast.Tuple) or (isinstance(e, ast.List) and in_slice):
        return [w for x in e.elts for w in _type_expr(x, index, facts, in_slice)]
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
        sides = (e.left, e.right)
        out = [w for x in sides for w in _type_expr(x, index, facts, in_slice)]
        if any(isinstance(x, ast.Constant) and x.value is not None for x in sides) or all(
                isinstance(x, ast.Constant) for x in sides):
            out.append(f"`{_snip(e)}`: `|` needs a type on each side (a TypeError)")
        return out
    return [f"a {type(e).__name__} (`{_snip(e)}`) is evaluated at import"]


# ------------------------------------------------------------------ files

def files_problems(changes: List[Tuple[str, str, str, str, str, str]],
                   blob) -> Tuple[List[str], List[str], int]:
    """(refusals, the modules to check, the other paths allowed)."""
    out: List[str] = []
    modules: List[str] = []
    other = 0
    what = {"A": "added", "D": "deleted", "T": "changed in type", "M": "modified"}
    for st, path, om, nm, ob, nb in changes:
        if path == TYPE_CHECKS:
            out.append(f"{path}:1: a typing commit leaves it (a shrinking list never needs it)")
        elif path.startswith(PACKAGE_DIR) and path.endswith(".py"):
            if st != "M":
                out.append(f"{path}:1: {what.get(st, st)}: a typing commit only modifies modules")
            elif om != nm or nm not in ("100644", "100755"):
                out.append(f"{path}:1: its mode changed ({om} to {nm}) or it is no regular file")
            else:
                modules.append(path)
        elif path == "pyproject.toml":
            if st != "M" or om != nm:
                out.append(f"{path}:1: {what.get(st, st)}: only its ratchet may change")
            else:
                out += pyproject_problems(blob(ob), blob(nb))
                other += 1
        elif path == "backlog.md" or (path.startswith(("docs/", "audits/")) and path.endswith(".md")):
            if {om, nm} - {"000000", "100644", "100755"}:
                out.append(f"{path}:1: no regular file (mode {nm})")
            else:
                other += 1
        else:
            out.append(f"{path}:1: not a path a typing commit may change")
    return out, modules, other


def _canon(v: object) -> object:
    """A TOML value as compared: tables by key, every scalar with its type."""
    if isinstance(v, dict):
        return ("table", tuple(sorted((k, _canon(x)) for k, x in v.items())))
    if isinstance(v, list):
        return ("array", tuple(_canon(x) for x in v))
    return (type(v).__name__, repr(v))


def _toml_difference(a: object, b: object, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if _canon(a.get(k)) != _canon(b.get(k)):
                return _toml_difference(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    return path or "the file"


def pyproject_problems(base: bytes, head: bytes) -> List[str]:
    """pyproject.toml: equal but for the ratchet, which only shrinks."""
    import tomllib
    try:
        b, h = (tomllib.loads(x.decode("utf-8")) for x in (base, head))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return [f"pyproject.toml:1: does not parse: {e}"]
    bm, hm = (copy.deepcopy(d.get("tool", {}).get("mypy", {})) for d in (b, h))
    for d in (b, h):
        if isinstance(d.get("tool"), dict):
            d["tool"].pop("mypy", None)
    if _canon(b) != _canon(h):
        return [f"pyproject.toml:1: {_toml_difference(b, h)} changed: only the ratchet may"]
    if not isinstance(bm, dict) or not isinstance(hm, dict):
        return ["pyproject.toml:1: [tool.mypy] is no table"]
    bo, ho = bm.pop("overrides", []), hm.pop("overrides", [])
    if _canon(bm) != _canon(hm):
        return [f"pyproject.toml:1: tool.mypy.{_toml_difference(bm, hm)} changed: only the "
                f"ratchet may"]

    def split(over: object) -> Tuple[list, list]:
        items = over if isinstance(over, list) else [over]
        ign = [o for o in items if isinstance(o, dict) and o.get("ignore_errors") is True]
        return [o for o in items if o not in ign], ign
    (b_rest, b_ign), (h_rest, h_ign) = split(bo), split(ho)
    if _canon(b_rest) != _canon(h_rest):
        return ["pyproject.toml:1: a tool.mypy.overrides table other than the ratchet changed"]
    if len(b_ign) > 1 or len(h_ign) > 1 or (h_ign and not b_ign):
        return ["pyproject.toml:1: the ratchet is one tool.mypy.overrides table with "
                "ignore_errors = true, and HEAD may only shrink BASE's"]
    if not h_ign:
        return []

    def modules(o: dict) -> list:
        m = o.get("module", [])
        return m if isinstance(m, list) else [m]
    rest_b = {k: v for k, v in b_ign[0].items() if k != "module"}
    rest_h = {k: v for k, v in h_ign[0].items() if k != "module"}
    if _canon(rest_b) != _canon(rest_h):
        return ["pyproject.toml:1: the ratchet's table changed beyond its module list"]
    grown = sorted(set(map(str, modules(h_ign[0]))) - set(map(str, modules(b_ign[0]))))
    if grown or len(modules(h_ign[0])) != len(set(map(str, modules(h_ign[0])))):
        return [f"pyproject.toml:1: the ratchet's module list grew: {', '.join(grown) or 'a repeat'}"]
    return []


# --------------------------------------------------------------- snapshot

_CHILD = r'''
import sys
SRC = sys.argv[1]
sys.path.insert(0, SRC)
import dataclasses, functools, hashlib, importlib, inspect, json, os, re, types
SPEC = json.loads(sys.argv[2])
TREE = os.path.dirname(SRC)
ADDR = re.compile(r" at 0x[0-9a-fA-F]+")
LIMIT = 2000
SKIP_KEYS = ("__annotations__", "__firstlineno__")
NO_VALUE = SKIP_KEYS + ("__doc__", "__dataclass_fields__", "__dataclass_params__")
PARAMS = ("init", "repr", "eq", "order", "unsafe_hash", "frozen", "match_args", "kw_only",
          "slots", "weakref_slot")

def norm(s):
    return ADDR.sub(" at 0x0", s).replace(SRC, "<src>").replace(TREE, "<tree>")

def stable(v, depth=0):
    if depth > 8:
        return "<deep>"
    t = type(v)
    if t in (set, frozenset):
        return t.__name__ + "({" + ", ".join(sorted(stable(x, depth + 1) for x in v)) + "})"
    if t is dict:
        return "{" + ", ".join(stable(k, depth + 1) + ": " + stable(x, depth + 1)
                               for k, x in v.items()) + "}"
    if t in (list, tuple):
        return t.__name__ + "(" + ", ".join(stable(x, depth + 1) for x in v) + ")"
    try:
        return repr(v)
    except Exception as e:
        return "<repr failed: " + type(e).__name__ + ">"

def bounded(v):
    s = norm(stable(v))
    if len(s) > LIMIT:
        s = s[:LIMIT] + "... sha256 " + hashlib.sha256(s.encode("utf-8", "backslashreplace")).hexdigest()
    return s

def qual(v):
    return str(getattr(v, "__module__", None)) + "." + str(getattr(v, "__qualname__", None))

def kind(v):
    if isinstance(v, types.ModuleType):
        return ["module", v.__name__]
    if isinstance(v, type):
        return ["class", qual(v)]
    if isinstance(v, types.FunctionType):
        return ["function", qual(v)]
    if isinstance(v, (types.BuiltinFunctionType, types.BuiltinMethodType)):
        return ["builtin", qual(v)]
    return ["other", qual(type(v)), bounded(v)]

def fn(f, depth=0):
    rec = {"qualname": f.__qualname__, "module": f.__module__, "defaults": bounded(f.__defaults__),
           "kwdefaults": bounded(f.__kwdefaults__),
           "attrs": sorted(k for k in vars(f) if k != "__wrapped__")}
    try:
        sig = inspect.signature(f, follow_wrapped=False)
        rec["signature"] = [[p.name, p.kind.name, None if p.default is p.empty else bounded(p.default)]
                            for p in sig.parameters.values()]
    except (TypeError, ValueError) as e:
        rec["signature"] = "<none: " + type(e).__name__ + ">"
    w = vars(f).get("__wrapped__")
    if w is not None:
        rec["wrapped"] = fn(w, depth + 1) if isinstance(w, types.FunctionType) and depth < 5 else kind(w)
    return rec

def member(v, owner, mod, out, depth):
    if isinstance(v, types.FunctionType):
        return ["function", fn(v)]
    if isinstance(v, (staticmethod, classmethod)):
        return [type(v).__name__, member(v.__func__, owner, mod, out, depth)]
    if isinstance(v, property):
        return ["property"] + [None if x is None else member(x, owner, mod, out, depth)
                               for x in (v.fget, v.fset, v.fdel)]
    if isinstance(v, functools.cached_property):
        return ["cached_property", member(v.func, owner, mod, out, depth)]
    if isinstance(v, functools._lru_cache_wrapper):
        return ["lru_cache", bounded(v.cache_parameters()), member(v.__wrapped__, owner, mod, out, depth)]
    if isinstance(v, type) and getattr(v, "__module__", None) == mod and depth < 6 and (
            v.__qualname__.startswith(owner + ".") if owner else "." not in v.__qualname__):
        if v.__qualname__ not in out["classes"]:
            cls(v, mod, out, depth + 1)
    return kind(v)

def field(f):
    ft = getattr(f, "_field_type", None)
    return {"name": f.name, "kind": getattr(ft, "name", repr(ft)),
            "default": None if f.default is dataclasses.MISSING else bounded(f.default),
            "factory": None if f.default_factory is dataclasses.MISSING else qual(f.default_factory),
            "init": f.init, "repr": f.repr, "compare": f.compare, "hash": bounded(f.hash),
            "kw_only": bounded(f.kw_only), "metadata": bounded(dict(f.metadata))}

def cls(c, mod, out, depth=0):
    d = vars(c)
    rec = {"mro": [qual(k) for k in c.__mro__], "keys": sorted(k for k in d if k not in SKIP_KEYS),
           "members": {}}
    out["classes"][c.__qualname__] = rec
    for k in sorted(d):
        if k not in NO_VALUE:
            rec["members"][k] = member(d[k], c.__qualname__, mod, out, depth)
    if "__dataclass_params__" in d:
        rec["params"] = {n: bounded(getattr(d["__dataclass_params__"], n, None)) for n in PARAMS}
    if "__dataclass_fields__" in d:
        rec["fields"] = [field(f) for f in d["__dataclass_fields__"].values()]

def expected(how, v):
    if how[0] == "any":
        return True
    try:
        m = importlib.import_module(how[1])
    except ImportError:
        return False
    return v is (m if how[0] == "module" else getattr(m, how[2], object()))

def record(m, skip):
    out = {"names": {}, "classes": {}}
    for k, v in sorted(vars(m).items()):
        if k == "__annotations__" or (k in skip and expected(skip[k], v)):
            continue
        if k == "__builtins__":
            out["names"][k] = ["builtins"]
        elif k in ("__file__", "__cached__", "__path__"):
            out["names"][k] = ["path", norm(str(v if k != "__path__" else list(v)))]
        elif k == "__spec__":
            out["names"][k] = ["spec", getattr(v, "name", None), norm(str(getattr(v, "origin", None)))]
        elif k == "__loader__":
            out["names"][k] = ["loader", type(v).__qualname__]
        else:
            out["names"][k] = member(v, "", m.__name__, out, 0)
    return out

out = {"modules": {}, "outside": []}
loaded = {}
for name in SPEC["modules"]:
    try:
        loaded[name] = importlib.import_module(name)
    except BaseException as e:
        out["modules"][name] = {"error": (type(e).__name__ + ": " + str(e))[:300]}
for name, m in loaded.items():
    try:
        out["modules"][name] = record(m, SPEC["skip"].get(name, {}))
    except Exception as e:
        out["modules"][name] = {"error": "recording it failed: " + type(e).__name__ + ": " + str(e)[:200]}
for k in sorted(sys.modules):
    if k.split(".")[0] in SPEC["tops"]:
        f = getattr(sys.modules[k], "__file__", None)
        if not isinstance(f, str) or not os.path.abspath(f).startswith(SRC + os.sep):
            out["outside"].append([k, f])
json.dump(out, open(sys.argv[3], "w", encoding="utf-8"), sort_keys=True)
assert not out["outside"], "imported from outside " + SRC + ": " + repr(out["outside"])
'''


def child_env(home: Path) -> Dict[str, str]:
    """A snapshot child's environment: none of ours that steers Python, git,
    taxjson or the XDG folders; HOME a new empty folder; offline."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TAXJSON_", "PYTHON", "GIT_", "XDG_"))}
    env.update(HOME=str(home), TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               XDG_CACHE_HOME=str(home / ".cache"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local" / "share"))
    return env


def skips(facts: Facts, tree: ast.Module,
          aliases: List[Tuple[str, int, ast.expr, int]]) -> Dict[str, List[str]]:
    """The names a side's snapshot leaves out when they hold what the
    bindings stage proved: typing's objects, the value of a TypeAlias HEAD
    adds (ALIASES; BASE's are recorded like any name), the future import's
    feature."""
    out = {name: list(src) for name, src in facts.typing_bound.items() if src[0] != "star"}
    out.update({name: ["any"] for name, _l, _v, _i in aliases})
    if has_future(tree):
        out["annotations"] = ["attr", "__future__", "annotations"]
    return out


def run_child(src: Path, modules: List[str], skip: Dict[str, Dict[str, List[str]]],
              td: Path, tag: str) -> dict:
    """Import MODULES from SRC in a fresh interpreter; what it records."""
    home = Path(tempfile.mkdtemp(prefix=f"home-{tag}-", dir=td))
    out = td / f"snapshot-{tag}.json"
    spec = {"modules": modules, "skip": skip, "tops": sorted({m.split(".")[0] for m in modules})}
    try:
        r = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", _CHILD, str(src),
                            json.dumps(spec), str(out)], cwd=home, env=child_env(home),
                           capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"the import took over {TIMEOUT} s"}
    got = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
    if got is None:
        return {"error": (r.stderr or r.stdout).strip()[-400:] or f"exit {r.returncode}"}
    return got


def json_difference(a: object, b: object, path: Tuple = ()) -> Tuple[Tuple, object, object]:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                return json_difference(a.get(k), b.get(k), path + (k,))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return json_difference(x, y, path + (i,))
    return path, a, b


def _def_lines(tree: ast.Module) -> Dict[str, int]:
    out: Dict[str, int] = {}

    def walk(body: Sequence[ast.stmt], prefix: str) -> None:
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.setdefault(prefix + st.name, st.lineno)
                if isinstance(st, ast.ClassDef):
                    walk(st.body, prefix + st.name + ".")
            elif isinstance(st, (ast.Assign, ast.AnnAssign)):
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    if isinstance(t, ast.Name):
                        out.setdefault(prefix + t.id, st.lineno)
    walk(tree.body, "")
    return out


def snapshot_problems(base_src: Path, head_src: Path, pairs: List[Pair], td: Path) -> List[str]:
    modules = sorted(p.module for p in pairs)
    by_module = {p.module: p for p in pairs}
    sides = []
    for tag, src in (("BASE", base_src), ("HEAD", head_src)):
        skip = {p.module: skips(*((p.base_facts, p.base, []) if tag == "BASE" else
                                  (p.head_facts, p.head, p.new_aliases))) for p in pairs}
        sides.append(run_child(src, modules, skip, td, tag))
    out: List[str] = []
    first = pairs[0].rel
    for tag, got in zip(("BASE", "HEAD"), sides):
        if "error" in got:
            out.append(f"{first}:1: {tag}'s snapshot failed: {got['error']}")
        for name, where in got.get("outside", []):
            out.append(f"{first}:1: {tag}'s snapshot imported {name} from {where}, not from "
                       f"its tree's src")
    if out:
        return out
    for m in modules:
        p, b, h = by_module[m], sides[0]["modules"].get(m, {}), sides[1]["modules"].get(m, {})
        for tag, got in (("BASE", b), ("HEAD", h)):
            if "error" in got:
                out.append(f"{p.rel}:1: {tag}'s import of {m} fails: {got['error']}")
        if "error" in b or "error" in h or b == h:
            continue
        path, x, y = json_difference(b, h)
        line = _def_lines(p.head).get(str(path[1]) if len(path) > 1 else "", 1)
        where = ".".join(str(k) for k in path)
        out.append(f"{p.rel}:{line}: {m}: {where} is {_short(y, 120)} in HEAD, "
                   f"{_short(x, 120)} in BASE")
    return out


# -------------------------------------------------------------------- git

def _genv() -> Dict[str, str]:
    """git's environment: replace objects off, none of our GIT_ variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_NO_REPLACE_OBJECTS": "1"}


def git(repo: Path, *args: str, data: Optional[bytes] = None) -> bytes:
    return subprocess.run(["git", "--no-replace-objects", *args], cwd=repo, input=data,
                          capture_output=True, check=True, env=_genv()).stdout


def resolve(repo: Path, rev: str, trees: bool) -> str:
    for kind in ("commit",) + (("tree",) if trees else ()):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{{kind}}}"],
                           cwd=repo, capture_output=True, text=True, env=_genv())
        if r.returncode == 0:
            return r.stdout.strip()
    raise Refused(f"{rev!r} is no commit" + (" or tree" if trees else ""))


def history_problem(repo: Path) -> Optional[str]:
    """Why git's view of REPO may not be its objects: replace refs, grafts."""
    if git(repo, "for-each-ref", "--count=1", "refs/replace/").strip():
        return "the repository has replace refs (refs/replace/)"
    grafts = git(repo, "rev-parse", "--git-path", "info/grafts").decode().strip()
    if (repo / grafts).exists():
        return f"the repository has grafts ({grafts})"
    return None


def changes(repo: Path, base: str, head: str) -> List[Tuple[str, str, str, str, str, str]]:
    """(status, path, BASE's mode, HEAD's mode, BASE's blob, HEAD's blob)
    for each path that differs."""
    parts = git(repo, "diff-tree", "-r", "-z", "--raw", "--no-renames", base, head).split(b"\0")
    out = []
    for meta, path in zip(parts[0::2], parts[1::2]):
        if not meta:
            break
        om, nm, ob, nb, st = meta.decode()[1:].split()
        out.append((st[0], path.decode("utf-8", "surrogateescape"), om, nm, ob, nb))
    return out


def blobs(repo: Path, oids: Sequence[str]) -> Dict[str, bytes]:
    oids = sorted(set(oids))
    if not oids:
        return {}
    raw = git(repo, "cat-file", "--batch", data="".join(f"{o}\n" for o in oids).encode())
    out: Dict[str, bytes] = {}
    i = 0
    for o in oids:
        nl = raw.index(b"\n", i)
        size = int(raw[i:nl].split()[2])
        out[o] = raw[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return out


def materialize_src(repo: Path, rev: str, dest: Path) -> Path:
    """REV's src/ under DEST, each blob's bytes as git holds them."""
    entries = []
    for rec in git(repo, "ls-tree", "-r", "-z", "--full-tree", rev, "--", "src").split(b"\0"):
        if rec:
            meta, path = rec.split(b"\t", 1)
            mode, _kind, oid = meta.decode("ascii").split()
            f = path.decode("utf-8", "surrogateescape")
            if mode not in ("100644", "100755"):
                raise Refused(f"{f}: a symlink or a submodule (mode {mode}) in src/")
            if f.startswith("/") or ".." in Path(f).parts or "\n" in f:
                raise Refused(f"{f!r}: not a path inside the tree")
            entries.append((f, oid))
    data = blobs(repo, [o for _f, o in entries])
    for f, oid in entries:
        q = dest / f
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(data[oid])
    return dest / "src"


# ------------------------------------------------------------------ check

def _stage_line(name: str, found: List[str], passed: str) -> None:
    if not found:
        print(f"==> {name}: passed, {passed}")
        return
    print(f"==> {name}: FAILED, {len(found)} refusal(s)")
    for p in found[:SHOWN]:
        print(f"- {p}")
    if len(found) > SHOWN:
        print(f"- and {len(found) - SHOWN} more")


def _plural(n: int, what: str) -> str:
    return f"{n} {what}" + ("" if n == 1 else "s")


def check(repo: Path, base: str, head: str) -> int:
    """The proof, BASE's copy running: the verdict line and its exit status."""
    failed: List[str] = []
    changed = changes(repo, base, head)
    print(f"==> Checking HEAD {head[:12]} against BASE {base[:12]}: "
          f"{_plural(len(changed), 'path')} changed")
    regular = ("100644", "100755")
    data = blobs(repo, [c[4] for c in changed if c[2] in regular]
                 + [c[5] for c in changed if c[3] in regular])
    found, rels, other = files_problems(changed, lambda o: data[o])
    _stage_line("files", found, f"{_plural(len(rels), 'module')} and {_plural(other, 'other path')}")
    if found:
        failed.append("files")
    by_rel = {c[1]: c for c in changed}
    pairs = [Pair(rel, data[by_rel[rel][4]], data[by_rel[rel][5]]) for rel in rels]
    for p in pairs:
        p.run_static()
    for mode in ("future", "typing", "unchanged"):
        names = [p.rel for p in pairs if p.mode == mode]
        if names:
            print(f"==> {mode} mode: {', '.join(names)}")
    count = sum(p.compared for p in pairs)
    passes = {"ast": f"{_plural(len(pairs), 'module')}",
              "bytecode": f"{_plural(count, 'code object')} compared",
              "purity": f"{_plural(sum(len(p.new_aliases) for p in pairs), 'new TypeAlias value')}",
              "bindings": "the typing names, cast, TYPE_CHECKING and the TypeAlias names"}
    for stage, passed in passes.items():
        found = [x for p in pairs for x in p.problems[stage]]
        _stage_line(stage, found, passed)
        if found:
            failed.append(stage)
    if failed:
        print("==> snapshot: not run: HEAD's code is run only once the stages before it pass")
    elif pairs:
        with tempfile.TemporaryDirectory(prefix="annotations-only-") as tdn:
            td = Path(tdn)
            srcs = [materialize_src(repo, rev, td / tag) for tag, rev in (("base", base), ("head", head))]
            found = snapshot_problems(srcs[0], srcs[1], pairs, td)
        _stage_line("snapshot", found, f"{_plural(len(pairs), 'module')} imported from each tree")
        if found:
            failed.append("snapshot")
    else:
        print("==> snapshot: passed, no module changed")
    print("annotations-only: " + (f"FAIL ({', '.join(failed)})" if failed else "PASS"))
    return 1 if failed else 0


def run_base_copy(repo: Path, base: str, head: str, flags: List[str]) -> int:
    """Run BASE's copy of this script: a typing commit cannot change its own proof."""
    with tempfile.TemporaryDirectory(prefix="annotations-only-tool-") as td:
        try:
            tar = git(repo, "archive", "--format=tar", base, TOOL)
        except subprocess.CalledProcessError:
            print(f"Error: BASE lacks {TOOL}: the tool lands in a commit of its own before a "
                  f"typing commit", file=sys.stderr)
            return 2
        subprocess.run(["tar", "-x", "-C", td], input=tar, check=True)
        me = Path(td) / TOOL
        env = {k: v for k, v in _genv().items() if not k.startswith("PYTHON")}
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(me), "--check", base, head,
                               f"--repo={repo}", "--base-copy", *flags], env=env).returncode


def explain(path: Path) -> int:
    tree = ast.parse(path.read_bytes(), str(path))
    print(f"==> {path}: the typing mode's normalization (the future import "
          f"{'present' if has_future(tree) else 'absent'})")
    print(ast.dump(normalize(tree, Facts(tree)), indent=1))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"Error: {TOOL} needs Python 3.12 or later, this is "
              f"{sys.version_info[0]}.{sys.version_info[1]}: run it with the dev venv's python3",
              file=sys.stderr)
        return 2
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__.split("\n\n")[1]
    repo, flags, args = Path("."), [], []
    for a in argv:
        if a.startswith("--repo="):
            repo = Path(a[len("--repo="):])
        elif a in ("--check", "--explain", "--trees", "--base-copy"):
            flags.append(a)
        else:
            args.append(a)
    if "--explain" in flags:
        if len(args) != 1 or len(flags) != 1 or args[0].startswith("--"):
            print(usage, file=sys.stderr)
            return 2
        return explain(Path(args[0]))
    if "--check" not in flags or len(args) != 2 or any(a.startswith("--") for a in args):
        print(usage, file=sys.stderr)
        return 2
    repo = repo.resolve()
    try:
        bad = history_problem(repo)
        if bad:
            raise Refused(f"{bad}: git would not show the objects themselves")
        base, head = (resolve(repo, r, "--trees" in flags) for r in args)
        if "--trees" not in flags and subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, head], cwd=repo,
                env=_genv()).returncode:
            raise Refused(f"BASE {base[:12]} is not an ancestor of HEAD {head[:12]}")
        if "--base-copy" not in flags:
            return run_base_copy(repo, base, head, [f for f in flags if f == "--trees"])
        return check(repo, base, head)
    except (Refused, subprocess.CalledProcessError) as e:
        why = e.stderr.decode(errors="replace").strip() if isinstance(
            e, subprocess.CalledProcessError) and e.stderr else str(e)
        print(f"Error: {why}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

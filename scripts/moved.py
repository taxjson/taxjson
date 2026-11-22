#!/usr/bin/env python3
"""moved: where a test module or class went (PLAN.md step 9).

Usage: scripts/moved.py NAME...

NAME is an old module, class or test: test_fix_x, test_fix_x.TestY or
test_fix_x.TestY.test_z (a backlog or CHANGELOG reference), or the
module's path, tests/test_fix_x.py (`::` read as `.`, a `:LINE` and
backticks dropped). The answer is
read from the `Formerly:` lines scripts/move_tests.py writes: the last
lines of a moved module's docstring (`Formerly: test_fix_x`) and of a class
split out of one (`Formerly: test_fix_x.TestY`; each new file of a split
names the module in its docstring too: split_modules). Prints each
name's new place, `tests/rules/ca/sl/test_held.py TestY.test_z` (a split
module: each place it went); exit 1 when a name never moved, or when its
class or test is not in the module's new file.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

TESTS = Path(__file__).resolve().parent.parent / "tests"
PREFIX = "Formerly: "


def _formerly(doc) -> List[str]:
    return [ln[len(PREFIX):].strip() for ln in (doc or "").splitlines()
            if ln.startswith(PREFIX)]


def formerly_map(tests: Path = TESTS) -> Dict[str, List[Tuple[str, str]]]:
    """Each old name (`test_fix_x`, `test_fix_x.TestY`) -> the places it
    went: (the new file below tests/, the class there or "")."""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for p in sorted(tests.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = p.relative_to(tests).as_posix()
        for old in _formerly(ast.get_docstring(tree, clean=True)):
            out.setdefault(old, []).append((rel, ""))
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                for old in _formerly(ast.get_docstring(n, clean=True)):
                    out.setdefault(old, []).append((rel, n.name))
    return out


def split_modules(fmap: Dict[str, List[Tuple[str, str]]]) -> Dict[str, List[str]]:
    """The modules split by class: each that a class's line names
    (`Formerly: test_fix_x.TestY`) -> the new files its classes went to,
    and those whose docstring names it (a file that lost its classes'
    lines is still one of the split's)."""
    out: Dict[str, set] = {}
    for old, places in fmap.items():
        if "." in old:
            for rel, cls in places:
                if cls:
                    out.setdefault(old.split(".")[0], set()).add(rel)
    for old, rels in out.items():
        rels |= {rel for rel, cls in fmap.get(old, []) if not cls}
    return {k: sorted(v) for k, v in sorted(out.items())}


def test_classes(tree: ast.Module) -> List[str]:
    """The module-level classes of TREE that run tests, as the mover reads
    them (extract_support's Plan._runs_tests): a TestCase (a base named
    *TestCase, its own or a base's of the module's) with tests (a `test*`
    or `runTest` method or attribute) or with a base from elsewhere; or a
    class with tests on a base from elsewhere. A mixin runs none."""
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    out = []
    for name, c in classes.items():
        case = tests = other = False
        todo, seen = [c], {name}
        while todo:
            k = todo.pop()
            own = [n.name for n in k.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            own += [tg.id for n in k.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                    for tg in (n.targets if isinstance(n, ast.Assign) else [n.target])
                    if isinstance(tg, ast.Name)]
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
        if (case and (tests or other)) or (tests and other):
            out.append(name)
    return out


def normalize(name: str) -> str:
    """A path form (`tests/test_fix_x.py`, `test_fix_x.py::TestY`, a
    backlog's `tests/test_fix_x.py:120` or `:10-20`, backticks and all) as
    the dotted name."""
    name = name.strip().strip("`'\"")
    name = re.sub(r"[`'\"]?:\d+(?:[-:]\d+)?$", "", name)
    name = re.sub(r"^(\./)?tests/", "", name).replace("::", ".")
    return re.sub(r"\.py(?=\.|$)", "", name, count=1)


def _has(tree: ast.Module, cls: str, test: str = "") -> bool:
    """Class CLS in the module, and TEST in it or in a class of the same
    module it derives from (a base from elsewhere, not unittest's
    TestCase, is taken on trust)."""
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    seen = set()
    while cls in classes and cls not in seen:
        seen.add(cls)
        node = classes[cls]
        if not test or any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and n.name == test for n in node.body):
            return True
        names = [b.id if isinstance(b, ast.Name) else
                 b.attr if isinstance(b, ast.Attribute) else "" for b in node.bases]
        local = [n for n in names if n in classes]
        if local:
            cls = local[0]
            continue
        return any(n not in ("TestCase", "object") for n in names)
    return False


def where(name: str, fmap: Dict[str, List[Tuple[str, str]]],
          tests: Path = TESTS) -> List[str]:
    """NAME's new places, as `tests/<path> [Class[.test]]`; [] if it never
    moved, or if its class or test is not where the module went."""
    parts = normalize(name).split(".")

    def tree(rel: str) -> ast.Module:
        return ast.parse((tests / rel).read_text(encoding="utf-8"))
    if len(parts) >= 2 and ".".join(parts[:2]) in fmap:
        # A class moved on its own (a split): its tests follow it.
        rest = parts[2:]
        return [f"tests/{rel} {cls}" + (f".{rest[0]}" if rest else "")
                for rel, cls in fmap[".".join(parts[:2])]
                if not rest or _has(tree(rel), cls, rest[0])]
    if parts[0] in fmap:
        places = [rel for rel, cls in fmap[parts[0]] if not cls]
        if len(parts) == 1:
            return [f"tests/{rel}" for rel in places]
        tail = ".".join(parts[1:])
        return [f"tests/{rel} {tail}" for rel in places
                if _has(tree(rel), parts[1], parts[2] if len(parts) > 2 else "")]
    return []


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    fmap = formerly_map()
    rc = 0
    for name in argv:
        places = where(name, fmap)
        mod = normalize(name).split(".")[0]
        if not places and mod in fmap:
            print(f"{name}: not in {', '.join('tests/' + r for r, _c in fmap[mod])}, "
                  f"where {mod} went")
            rc = 1
        elif not places:
            print(f"{name}: never moved (no `Formerly: {mod}` below tests/)")
            rc = 1
        for p in places:
            print(f"{name}: {p}")
    return rc


if __name__ == "__main__":
    sys.exit(main())

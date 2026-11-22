"""scripts/check_annotations_only.py (PLAN.md step 10 T0): the proof that a
typing commit changes no behaviour, on synthetic sources.

- The design's table (audits/step10-design/t0-design.md, Tests): each pair
  of BASE and HEAD sources, its verdict and the stages that refuse it, the
  snapshot run when the static stages pass, as --check runs it.
- The critique's holes, H1 to H6: the snapshot's interpreter reaching the
  dev clone through the editable install's .pth (H1); a bare annotation
  making a name local (H2); a quoted or aliased ClassVar (H3); a cast that
  is not typing's (H4); a typing name newer than Python 3.12 (H5); a
  TypeAlias value that runs code (H6).
- The soundness review's cases (round 2), in the table: BASE's TypeAlias
  removed or pointed elsewhere, a constant as a TypeAlias value, an
  annotation or a TYPE_CHECKING import in a class inside a function, a
  declared local; the rows the bytecode stage decides alone.
- The files stage and the ratchet; each stage alone where another would
  hide it; --check on commits of a temporary repository, run as BASE's
  copy; the arguments."""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _paths

TOOL = "scripts/check_annotations_only.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_annotations_only_under_test",
                                                  _paths.REPO / TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CAO = _load()
REL = "src/taxjson/m.py"
HEAD_DOC = '"""A synthetic module."""\n'
FUT = HEAD_DOC + "from __future__ import annotations\n\n"
MODEL = "class Lot:\n    def __init__(self, rows=None):\n        self.rows = rows\n"


def F(text):
    """A module with the docstring and the future import, then TEXT."""
    return FUT + textwrap.dedent(text).lstrip("\n")


def P(text):
    """A module with the docstring and no future import, then TEXT."""
    return HEAD_DOC + "\n" + textwrap.dedent(text).lstrip("\n")


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def _stages(base, head, extra=None, rel=REL):
    """The stages that refuse HEAD's REL against BASE's (the snapshot run
    when the static stages pass), and the Pair."""
    p = CAO.Pair(rel, base.encode(), head.encode())
    p.run_static()
    failed = {s for s, found in p.problems.items() if found}
    if not failed and p.mode:
        found = _snapshot(p, base, head, extra, rel)
        if found:
            failed.add("snapshot")
            p.problems["snapshot"] = found
    return failed, p


def _snapshot(p, base, head, extra=None, rel=REL):
    """The snapshot's refusals for the Pair P, whatever the static stages found."""
    with tempfile.TemporaryDirectory() as td:
        srcs = []
        for tag, text in (("base", base), ("head", head)):
            files = {"src/taxjson/__init__.py": "", "src/taxjson/model.py": MODEL,
                     rel: text, **(extra or {})}
            _write(Path(td) / tag, files)
            srcs.append(Path(td) / tag / "src")
        return CAO.snapshot_problems(srcs[0], srcs[1], [p], Path(td))


def _text(p):
    return "\n".join(x for found in p.problems.values() for x in found)


# (what, BASE, HEAD, the stages that refuse it: none passes, a fragment of the refusal)
PASSES = [
    ("annotations on arguments and returns, nested defs included",
     F('''
       def f(a, /, b=1, *args, c, **kw):
           def g(x, y=None):
               return x
           return g(a), lambda z: z
       '''),
     F('''
       from typing import Any, Optional


       def f(a: int, /, b: int = 1, *args: str, c: Optional[Lot], **kw: Any) -> tuple:
           def g(x: "Lot", y: Optional[int] = None) -> Lot:
               return x
           return g(a), lambda z: z
       ''')),
    ("x: int = 0 and x: List[Lot] = [] in a function where x was assigned",
     F('''
       class K:
           def __init__(self):
               self.rows = {}

           def f(self):
               x = 0
               y = []
               y.append(x)
               return y
       '''),
     F('''
       from typing import Dict, List


       class K:
           def __init__(self) -> None:
               self.rows: Dict[str, Lot] = {}

           def f(self) -> List[Lot]:
               x: int = 0
               y: List[Lot] = []
               y.append(x)
               return y
       ''')),
    ("typing and collections.abc imports added",
     F('''
       def f(a):
           return a
       '''),
     F('''
       import typing
       from collections.abc import Iterator
       from typing import Dict


       def f(a: Dict[str, int]) -> Iterator[typing.Any]:
           return a
       ''')),
    ("an `if TYPE_CHECKING:` import, its name in an unquoted annotation",
     F('''
       def f(lot):
           return lot
       '''),
     F('''
       from typing import TYPE_CHECKING

       if TYPE_CHECKING:
           from taxjson.nowhere import Lot


       def f(lot: Lot) -> Lot:
           return lot
       ''')),
    ("a TYPE_CHECKING block's else body takes its place",
     F('''
       Lot = object
       '''),
     F('''
       import typing

       if typing.TYPE_CHECKING:
           from taxjson.nowhere import Lot
       else:
           Lot = object
       ''')),
    ("TypeAlias statements",
     F('''
       from typing import Dict

       from taxjson.model import Lot


       def f(rows):
           return Lot(rows)
       '''),
     F('''
       from typing import Callable, Dict, Optional, TypeAlias

       from taxjson.model import Lot

       Rows: TypeAlias = Dict[str, Lot]
       Maker: TypeAlias = Callable[[str], Optional["Lot"]]
       Either: TypeAlias = Lot | None
       Pairs: TypeAlias = list[tuple[Lot, ...]]


       def f(rows: Rows) -> Lot:
           return Lot(rows)
       ''')),
    ("a dataclass field's annotation changed but not its kind",
     F('''
       from dataclasses import dataclass, field
       from typing import Any, ClassVar, Dict


       @dataclass
       class Holder:
           KIND: ClassVar[str] = "h"
           rows: Dict[Any, Any] = field(default_factory=dict)
           n: Any = 0
       '''),
     F('''
       from dataclasses import dataclass, field
       from typing import Any, ClassVar, Dict


       @dataclass
       class Holder:
           KIND: ClassVar[Any] = "h"
           rows: Dict[str, "Lot"] = field(default_factory=dict)
           n: int = 0
       ''')),
    ("cast(\"T\", v) in a body, by name and through typing",
     F('''
       import typing


       def f(v):
           return v + 1, [v for v in v]
       '''),
     F('''
       import typing
       from typing import cast


       def f(v):
           return cast("int", v) + 1, [typing.cast("str", v) for v in v]
       ''')),
    ("a plain class's x = v becoming x: T = v, a bare x: T added",
     F('''
       class K:
           n = 0
           m = None
       '''),
     F('''
       from typing import Optional


       class K:
           n: int = 0
           m: Optional[str] = None
           k: int
       ''')),
    ("the future import added alone, after the docstring",
     P('''
       import dataclasses
       from typing import ClassVar

       X: int = 1


       @dataclasses.dataclass
       class D:
           KIND: ClassVar[str] = "d"
           x: int = 0


       def f(a: int) -> int:
           return a
       '''),
     F('''
       import dataclasses
       from typing import ClassVar

       X: int = 1


       @dataclasses.dataclass
       class D:
           KIND: ClassVar[str] = "d"
           x: int = 0


       def f(a: int) -> int:
           return a
       ''')),
    ("the future import added alone, to a module with no docstring",
     "import os\n",
     "from __future__ import annotations\n\nimport os\n"),
    ("a comment changed in a module without the future import",
     P("x = 1  # one\n"), P("x = 1  # uno\n")),
    ("a method named cast, and an unquoted typing cast BASE has, kept",
     F("from typing import cast\n\n\ndef f(o):\n    return o.cast(1), cast(int, o)\n"),
     F("from typing import cast\n\n\ndef f(o: Lot) -> tuple:\n    return o.cast(1), cast(int, o)\n")),
    ("a declared local: x: Optional[int] before the branches that assign it",
     F('''
       def f(c):
           if c:
               x = 1
           else:
               x = None
           return x
       '''),
     F('''
       from typing import Optional


       def f(c: bool) -> Optional[int]:
           x: Optional[int]
           if c:
               x = 1
           else:
               x = None
           return x
       ''')),
    ("BASE's TypeAlias kept and read at run time; an assignment becoming a TypeAlias",
     F('''
       from decimal import Decimal
       from typing import Dict, TypeAlias

       Num: TypeAlias = Decimal
       Rows = Dict[str, int]


       def f(x):
           return isinstance(x, Num), Rows
       '''),
     F('''
       from decimal import Decimal
       from typing import Dict, TypeAlias

       Num: TypeAlias = Decimal
       Rows: TypeAlias = Dict[str, int]


       def f(x: object) -> tuple:
           return isinstance(x, Num), Rows
       ''')),
    ("TypeAlias values: through a module's alias, a forward reference, None",
     F("import decimal as M\n"),
     F('''
       import decimal as M
       from typing import TypeAlias

       A: TypeAlias = M.Decimal
       S: TypeAlias = "Lot"
       N: TypeAlias = None
       ''')),
    ("a class inside a function keeping BASE's annotation, its method typed",
     F('''
       def make():
           rate = "enclosing"

           class Row:
               shown: str = rate

               def get(self):
                   return self.shown
           return Row
       '''),
     F('''
       def make() -> type:
           rate = "enclosing"

           class Row:
               shown: str = rate

               def get(self) -> str:
                   return self.shown
           return Row
       ''')),
]

REFUSALS = [
    ("a default changed",
     F("def f(a=1):\n    return a\n"), F("def f(a: int = 2):\n    return a\n"),
     {"ast", "bytecode"}, "is not BASE's"),
    ("a statement added",
     F("def f(a):\n    return a\n"), F("def f(a: int):\n    a += 1\n    return a\n"),
     {"ast", "bytecode"}, "HEAD adds `a += 1`"),
    ("a docstring changed",
     F('def f(a):\n    """One."""\n    return a\n'), F('def f(a: int):\n    """Two."""\n    return a\n'),
     {"ast", "bytecode"}, "is not BASE's"),
    ("in a dataclass, x = v becoming x: T = v",
     F("from dataclasses import dataclass\n\n\n@dataclass\nclass D:\n    x = 0\n"),
     F("from dataclasses import dataclass\n\n\n@dataclass\nclass D:\n    x: int = 0\n"),
     {"ast", "bytecode"}, "is not BASE's `x = 0`"),
    ("a ClassVar field becoming a plain field",
     F("from dataclasses import dataclass\nfrom typing import ClassVar\n\n\n@dataclass\n"
       "class D:\n    x: ClassVar[int] = 0\n"),
     F("from dataclasses import dataclass\nfrom typing import ClassVar\n\n\n@dataclass\n"
       "class D:\n    x: int = 0\n"),
     {"snapshot"}, "_FIELD"),
    ("a plain field becoming CV[int], CV bound to ClassVar",
     F("from dataclasses import dataclass\nfrom typing import ClassVar as CV\n\n\n@dataclass\n"
       "class D:\n    x: int = 0\n"),
     F("from dataclasses import dataclass\nfrom typing import ClassVar as CV\n\n\n@dataclass\n"
       "class D:\n    x: CV[int] = 0\n"),
     {"snapshot"}, "_FIELD_CLASSVAR"),
    ("a plain field becoming a bare ClassVar",
     F("from dataclasses import dataclass\nfrom typing import ClassVar\n\n\n@dataclass\n"
       "class D:\n    x: int = 0\n"),
     F("from dataclasses import dataclass\nfrom typing import ClassVar\n\n\n@dataclass\n"
       "class D:\n    x: ClassVar = 0\n"),
     {"snapshot"}, "_FIELD_CLASSVAR"),
    ("a bare x: int in a function where x is otherwise global",
     F("X = 1\n\n\ndef f():\n    return X\n"), F("X = 1\n\n\ndef f():\n    X: int\n    return X\n"),
     {"bytecode"}, "f: co_varnames is ('X',), BASE's ()"),
    ("a bare Y: int in a function whose list comprehension reads the global Y",
     F("Y = 2\n\n\ndef f(xs):\n    return [x * Y for x in xs]\n"),
     F("Y = 2\n\n\ndef f(xs):\n    Y: int\n    return [x * Y for x in xs]\n"),
     {"bytecode"}, "f: co_varnames is ('xs', 'x', 'Y'), BASE's ('xs', 'x')"),
    ("a bare Y: int in a function whose generator expression reads the global Y",
     F("Y = 2\n\n\ndef f(xs):\n    return sum(x * Y for x in xs)\n"),
     F("Y = 2\n\n\ndef f(xs):\n    Y: int\n    return sum(x * Y for x in xs)\n"),
     {"bytecode"}, "f.<locals>.<genexpr>: co_freevars is ('Y',), BASE's ()"),
    ("a nested def's default reading a name a TYPE_CHECKING import makes local",
     F("from typing import TYPE_CHECKING\n\nLot = 1\n\n\ndef f():\n    def g(a=Lot):\n"
       "        return a\n    return g\n"),
     F("from typing import TYPE_CHECKING\n\nLot = 1\n\n\ndef f():\n    if TYPE_CHECKING:\n"
       "        from taxjson.model import Lot\n\n    def g(a: Lot = Lot) -> Lot:\n"
       "        return a\n    return g\n"),
     {"bytecode"}, "f: co_varnames is ('Lot', 'g'), BASE's ('g',)"),
    ("a lambda under a function's TYPE_CHECKING block",
     F("from typing import TYPE_CHECKING\n\n\ndef f():\n    a = lambda: 1\n    return a\n"),
     F("from typing import TYPE_CHECKING\n\n\ndef f():\n    if TYPE_CHECKING:\n"
       "        k = lambda: 0\n    a = lambda: 1\n    return a\n"),
     {"bytecode"}, "HEAD adds code f.<locals>.<lambda>"),
    ("cast(Dict[str, X], v) unquoted",
     F("def f(v):\n    return v\n"),
     F("from typing import Dict, cast\n\n\ndef f(v):\n    return cast(Dict[str, int], v)\n"),
     {"ast", "bytecode"}, "cast(Dict[str, int], v)"),
    ("a parameter named cast elsewhere in the module",
     F("def f(v):\n    return v\n\n\ndef g(v, cast=None):\n    return v\n"),
     F("from typing import cast\n\n\ndef f(v):\n    return cast('int', v)\n\n\n"
       "def g(v, cast=None):\n    return v\n"),
     {"ast", "bytecode", "bindings"}, "cast is bound by a parameter (line 11)"),
    ("a local named cast",
     F("def f(v):\n    return v\n\n\ndef g():\n    cast = 1\n    return cast\n"),
     F("from typing import cast\n\n\ndef f(v):\n    return cast('int', v)\n\n\n"
       "def g():\n    cast = 1\n    return cast\n"),
     {"ast", "bytecode", "bindings"}, "cast is bound by an assignment"),
    ("a typing import removed while code reads it at run time",
     F("from typing import Dict\n\n\ndef f():\n    return Dict\n"),
     F("def f():\n    return Dict\n"),
     {"bindings"}, "HEAD no longer binds Dict from typing"),
    ("a new typing name shadowing a module binding",
     F("List = list\n\n\ndef f():\n    return List()\n"),
     F("from typing import List\n\nList = list\n\n\ndef f() -> List[int]:\n    return List()\n"),
     {"bindings"}, "List would shadow an assignment (line 4"),
    ("from typing import TypeIs (not in Python 3.12)",
     F("class A:\n    def m(self, x):\n        return x\n"),
     F("from typing import TypeIs\n\n\nclass A:\n    def m(self, x) -> TypeIs[int]:\n        return x\n"),
     {"bindings"}, "typing.TypeIs is not in Python 3.12's typing"),
    ("from typing_extensions import Self",
     F("class A:\n    def m(self):\n        return self\n"),
     F("from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from typing_extensions import Self\n"
       "\n\nclass A:\n    def m(self) -> Self:\n        return self\n"),
     {"bindings"}, "imports typing_extensions, which is not a dependency"),
    ("a TypeAlias rebinding a def BASE has: compared, BASE binding the name",
     F("from typing import Dict\n\n\ndef helper():\n    return 1\n"),
     F("from typing import Dict, TypeAlias\n\n\ndef helper():\n    return 1\n\n\n"
       "helper: TypeAlias = Dict[str, int]\n"),
     {"ast", "bytecode"}, "HEAD adds `helper = Dict[str, int]`"),
    ("a new TypeAlias rebinding a name HEAD imports",
     F("from typing import Dict\n"),
     F("from typing import Dict, TypeAlias\nfrom typing import List as Rows\n\n"
       "Rows: TypeAlias = Dict[str, int]\n"),
     {"bindings"}, "the TypeAlias Rows is bound by a typing import (line 5)"),
    ("a TypeAlias with a call in its value",
     F("from typing import Dict\n\n\ndef make():\n    return dict\n"),
     F("from typing import Dict, TypeAlias\n\n\ndef make():\n    return dict\n\n\n"
       "Rows: TypeAlias = make()\n"),
     {"purity"}, "a Call (`make()`) is evaluated at import"),
    ("the future import removed",
     F("def f(a):\n    return a\n"), P("def f(a):\n    return a\n"),
     {"ast"}, "HEAD removes `from __future__ import annotations`"),
    ("the future import added together with an annotation",
     P("def f(a):\n    return a\n"), F("def f(a: int):\n    return a\n"),
     {"ast"}, "add the future import first, in its own commit"),
    ("the future import added, not directly after the docstring",
     P("from __future__ import division\nimport os\n"),
     HEAD_DOC + "from __future__ import division\nfrom __future__ import annotations\nimport os\n",
     {"ast"}, "directly after the module docstring"),
    ("the future import merged into another future import",
     P("from __future__ import division\n"), HEAD_DOC + "from __future__ import division, annotations\n",
     {"ast"}, "HEAD adds the future import with other changes"),
    ("the future import added where one of BASE's annotations runs code",
     P("def make():\n    return int\n\n\ndef f(x: make()) -> None:\n    return x\n"),
     F("def make():\n    return int\n\n\ndef f(x: make()) -> None:\n    return x\n"),
     {"purity"}, "BASE evaluates the annotation `make()` (a Call in it), which the future import stops"),
    ("a module typed without the future import at BASE",
     P("def f(a):\n    return a\n"), P("def f(a: int) -> int:\n    return a\n"),
     {"ast"}, "BASE lacks `from __future__ import annotations`: add the future import first"),
    ("a non-typing import added outside TYPE_CHECKING",
     F("def f(a):\n    return a\n"),
     F("from taxjson.model import Lot\n\n\ndef f(a: Lot):\n    return a\n"),
     {"ast", "bytecode"}, "HEAD adds `from taxjson.model import Lot`"),
    ("@overload added",
     F("def f(a):\n    return a\n"),
     F("from typing import overload\n\n\n@overload\ndef f(a: int) -> int: ...\n\n\n"
       "def f(a):\n    return a\n"),
     {"ast", "bytecode"}, "HEAD adds"),
    ("a HEAD module whose import fails",
     F("from typing import Optional\n"),
     F("from typing import Optional, TypeAlias\n\nBad: TypeAlias = Optional[int, str]\n"),
     {"snapshot"}, "HEAD's import of taxjson.m fails"),
    ("an import under TYPE_CHECKING inside a function (it makes the name local)",
     F("from typing import TYPE_CHECKING\n\nLot = 1\n\n\ndef f():\n    return Lot\n"),
     F("from typing import TYPE_CHECKING\n\nLot = 1\n\n\ndef f():\n    if TYPE_CHECKING:\n"
       "        from taxjson.model import Lot\n    return Lot\n"),
     {"bytecode"}, "f: co_varnames is ('Lot',), BASE's ()"),
    ("BASE's TypeAlias removed while code reads it",
     F("from decimal import Decimal\nfrom typing import TypeAlias\n\nNum: TypeAlias = Decimal\n\n\n"
       "def f(x):\n    return isinstance(x, Num)\n"),
     F("from decimal import Decimal\nfrom typing import TypeAlias\n\n\n"
       "def f(x):\n    return isinstance(x, Num)\n"),
     {"ast", "bytecode"}, f"{REL}:7: HEAD drops `Num = Decimal`"),
    ("BASE's TypeAlias pointed elsewhere",
     F("from decimal import Decimal\nfrom typing import TypeAlias\n\nNum: TypeAlias = Decimal\n"),
     F("from decimal import Decimal\nfrom typing import TypeAlias\n\nNum: TypeAlias = float\n"),
     {"ast", "bytecode"}, "`Num = float` is not BASE's `Num = Decimal`"),
    ("a TypeAlias whose value is a number",
     F("import decimal as M\n"),
     F("import decimal as M\nfrom typing import TypeAlias\n\nA: TypeAlias = M.Decimal\n"
       "B: TypeAlias = 5\n"),
     {"purity"}, f"{REL}:8: the TypeAlias B: the constant 5 is no type"),
    ("an annotation added in a class in a class inside a function",
     F('rate = "global"\n\n\ndef make():\n    rate = "enclosing"\n\n    class Outer:\n'
       "        a = rate\n\n        class Row:\n            shown = rate\n"
       "    return Outer.Row.shown\n"),
     F('rate = "global"\n\n\ndef make():\n    rate = "enclosing"\n\n    class Outer:\n'
       "        a = rate\n\n        class Row:\n            rate: str\n            shown = rate\n"
       "    return Outer.Row.shown\n"),
     {"ast"}, f"{REL}:14: HEAD adds `rate: str` in make.<locals>.Outer.Row, a class inside a "
     "function"),
    ("a TYPE_CHECKING import added in a class in a class inside a function",
     F('from typing import TYPE_CHECKING\n\n\ndef make():\n    Lot = "enclosing"\n\n'
       "    class Outer:\n        a = Lot\n\n        class Row:\n            shown = Lot\n"
       "    return Outer.Row.shown\n"),
     F('from typing import TYPE_CHECKING\n\n\ndef make():\n    Lot = "enclosing"\n\n'
       "    class Outer:\n        a = Lot\n\n        class Row:\n"
       "            if TYPE_CHECKING:\n                from taxjson.model import Lot\n"
       "            shown = Lot\n    return Outer.Row.shown\n"),
     {"ast"}, "HEAD adds `if TYPE_CHECKING:` in make.<locals>.Outer.Row, a class inside a function"),
    ("an annotation added in a class inside a function",
     F('rate = "global"\n\n\ndef make():\n    rate = "enclosing"\n\n    class Row:\n'
       "        shown = rate\n    return Row.shown\n"),
     F('rate = "global"\n\n\ndef make():\n    rate = "enclosing"\n\n    class Row:\n'
       "        rate: str\n        shown = rate\n    return Row.shown\n"),
     {"ast", "bytecode"}, "the name the class body's own, read from the globals"),
]

# The rows the bytecode stage decides alone: what the ast stage normalizes away.
BYTECODE_ALONE = [row for row in REFUSALS if row[3] == {"bytecode"}]


class TestTheTable(unittest.TestCase):
    def test_what_passes(self):
        for what, base, head in PASSES:
            with self.subTest(what):
                failed, p = _stages(base, head)
                self.assertEqual(failed, set(), _text(p))
                self.assertIn(p.mode, ("typing", "future", "unchanged"))

    def test_the_modes(self):
        modes = {what: _stages(base, head)[1].mode for what, base, head in PASSES}
        self.assertEqual(modes["the future import added alone, after the docstring"], "future")
        self.assertEqual(modes["the future import added alone, to a module with no docstring"],
                         "future")
        self.assertEqual(modes["a comment changed in a module without the future import"],
                         "unchanged")
        self.assertEqual(modes["a dataclass field's annotation changed but not its kind"], "typing")

    def test_what_is_refused_and_by_which_stage(self):
        for what, base, head, stages, why in REFUSALS:
            with self.subTest(what):
                failed, p = _stages(base, head)
                self.assertEqual(failed, stages, _text(p))
                self.assertIn(why, _text(p))


class TestTheHoles(unittest.TestCase):
    """The critique's holes in the first design, each closed."""

    def test_h1_the_snapshot_imports_its_tree_not_the_dev_clone(self):
        # The dev venv's editable install puts the dev clone's src on
        # sys.path (a .pth): a child that read it would import the clone's
        # modules for any the tree lacks, or the clone's package for a
        # folder with no __init__.py.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, {"src/taxjson/__init__.py": "MARK = 'tree'\n",
                          "src/taxjson/only_here_7f3a.py": "X = 1\n",
                          "src/taxjson/reaches.py": "import taxjson.lib.country\n"})
            got = CAO.run_child(root / "src", ["taxjson", "taxjson.only_here_7f3a",
                                               "taxjson.reaches"], {}, root, "t")
            self.assertEqual(got["outside"], [])
            self.assertEqual(got["modules"]["taxjson"]["names"]["MARK"][2], "'tree'")
            self.assertIn("X", got["modules"]["taxjson.only_here_7f3a"]["names"])
            self.assertIn("ModuleNotFoundError", got["modules"]["taxjson.reaches"]["error"])
        # A package folder with no __init__.py: a namespace package, its
        # __file__ None: the child's assertion refuses it.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, {"src/taxjson/m.py": "X = 1\n"})
            got = CAO.run_child(root / "src", ["taxjson.m"], {}, root, "t")
            self.assertEqual(got["outside"], [["taxjson", None]])

    def test_h1_the_child_runs_isolated(self):
        # -I -S: no PYTHONPATH, no site (no .pth), no current folder; HOME a
        # new empty folder; offline, full width.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            probe = ("import os, sys\nENV = (os.environ['HOME'], os.environ['TAXJSON_OFFLINE'], "
                     "os.environ['TAXJSON_WIDTH'], 'PYTHONPATH' in os.environ, sys.flags.isolated, "
                     "sys.flags.no_site, os.listdir(os.environ['HOME']))\n")
            _write(root, {"src/taxjson/__init__.py": "", "src/taxjson/probe.py": probe})
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(root), "TAXJSON_OFFLINE": "0"}):
                got = CAO.run_child(root / "src", ["taxjson.probe"], {}, root, "t")
            env = got["modules"]["taxjson.probe"]["names"]["ENV"][2]
            self.assertRegex(env, r"^tuple\('<tree>/home-t-[^']+', '1', '0', False, 1, 1, list\(\)\)$")

    def test_h2_a_bare_annotation_that_makes_a_name_local(self):
        # In a function the ast stage drops a bare `x: T` on a name: the
        # functions' code, compiled with it, holds the scope. A declared
        # local (x assigned anyway) passes; a global or an enclosing
        # function's x turned local is the bytecode stage's alone.
        cases = [("a global", F("X = 1\n\n\ndef f():\n    return X\n"),
                  F("X = 1\n\n\ndef f():\n    X: int\n    return X\n"), {"bytecode"}, "co_varnames"),
                 ("an enclosing function's",
                  F("def f():\n    x = 1\n\n    def g():\n        return x\n    return g\n"),
                  F("def f():\n    x = 1\n\n    def g():\n        x: int\n        return x\n"
                    "    return g\n"), {"bytecode"}, "f: co_varnames is ('x', 'g'), BASE's ('g',)"),
                 ("an attribute's object evaluated",
                  F("def f(o):\n    return o\n"), F("def f(o):\n    o.x: int\n    return o\n"),
                  {"ast", "bytecode"}, "co_code"),
                 ("a declared local", F("def f():\n    x = 1\n    return x\n"),
                  F("def f():\n    x: int\n    x = 1\n    return x\n"), set(), "")]
        for what, base, head, stages, field in cases:
            with self.subTest(what):
                failed, p = _stages(base, head)
                self.assertEqual(failed, stages, _text(p))
                self.assertIn(field, _text(p))
        # At module level and in a plain class a bare annotation binds
        # nothing: dropped.
        failed, p = _stages(F("X = 1\n"), F("X = 1\nY: int\n"))
        self.assertEqual(failed, set(), _text(p))

    def test_h3_a_quoted_or_aliased_classvar(self):
        # dataclasses reads a field's kind from its annotation's text when it
        # is a string: `CV[int]` is a ClassVar when the module binds CV to
        # it, and under the future import a quoted "ClassVar[int]" is the
        # text `'ClassVar[int]'`, quotes and all: a plain field. Only the
        # snapshot sees which.
        dc = "from dataclasses import dataclass\nfrom typing import ClassVar\n\n\n@dataclass\nclass D:\n"
        # Typing mode: a quoted ClassVar stays the plain field it was.
        failed, p = _stages(F(dc + "    x: int = 0\n"), F(dc + '    x: "ClassVar[int]" = 0\n'))
        self.assertEqual(failed, set(), _text(p))
        # Future mode: the same quoted annotation was a ClassVar without
        # the future import, and is a field with it.
        failed, p = _stages(P(dc + '    x: "ClassVar[int]" = 0\n'), F(dc + '    x: "ClassVar[int]" = 0\n'))
        self.assertEqual(failed, {"snapshot"}, _text(p))
        self.assertIn("classes.D.fields", _text(p))
        self.assertIn(f"{REL}:9: taxjson.m: classes.D.fields.0.kind is '_FIELD' in HEAD, "
                      "'_FIELD_CLASSVAR' in BASE", _text(p))
        # An alias bound after a typing import of ClassVar.
        alias = dc.replace("ClassVar\n", "ClassVar\n\nCV = ClassVar\n")
        failed, p = _stages(F(alias + "    x: int = 0\n"), F(alias + "    x: CV[int] = 0\n"))
        self.assertEqual(failed, {"snapshot"}, _text(p))
        # InitVar and KW_ONLY change the fields too: an InitVar annotation
        # replaces another (a dataclass's annotations are markers to the ast
        # stage), so only the snapshot sees it; KW_ONLY is a bare annotation
        # added, a statement of the class body.
        dc2 = "from dataclasses import InitVar, KW_ONLY, dataclass\n\n\n@dataclass\nclass D:\n"
        for what, a, b, stages, why in (
                ("InitVar", "    x: int = 0\n", "    x: InitVar[int] = 0\n", {"snapshot"},
                 "classes.D.fields.0.kind is '_FIELD_INITVAR' in HEAD, '_FIELD' in BASE"),
                ("KW_ONLY", "    a: int = 0\n    b: int = 0\n",
                 "    a: int = 0\n    _: KW_ONLY\n    b: int = 0\n", {"ast", "bytecode"},
                 "HEAD adds `_: '<annotation>'`")):
            with self.subTest(what):
                failed, p = _stages(F(dc2 + a), F(dc2 + b))
                self.assertEqual(failed, stages, _text(p))
                self.assertIn(why, _text(p))

    def test_h4_a_cast_that_is_not_typings(self):
        use = "\n\ndef f(v):\n    return cast('int', v)\n"
        cases = [
            ("a class attribute named cast",
             F("from typing import cast\n\n\nclass K:\n    cast = 1\n\n\ndef f(v):\n    return v\n"),
             F("from typing import cast\n\n\nclass K:\n    cast = 1\n" + use),
             "cast is bound by an assignment (line 8)"),
            ("cast from another module",
             F("def f(v):\n    return v\n"), F("from taxjson.model import cast\n" + use),
             "cast is bound by an import from taxjson.model"),
            ("cast imported only under TYPE_CHECKING",
             F("from typing import TYPE_CHECKING\n\n\ndef f(v):\n    return v\n"),
             F("from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from typing import cast\n"
               + use), "cast is bound by an import from typing (line 7), not only"),
            ("cast stored as an attribute",
             F("import typing\n\n\ndef f(v):\n    return v\n"),
             F("import typing\n\ntyping.cast = print\n\n\ndef f(v):\n    return typing.cast('int', v)\n"),
             "no cast call is stripped: an attribute named cast is assigned in the module"),
        ]
        for what, base, head, why in cases:
            with self.subTest(what):
                failed, p = _stages(base, head)
                self.assertIn("ast", failed, _text(p))
                self.assertIn(why, _text(p))

    def test_h5_typing_names_newer_than_python_3_12(self):
        for name in ("ReadOnly", "TypeIs", "NoDefault", "get_protocol_members", "is_protocol"):
            with self.subTest(name):
                failed, p = _stages(F("X = 1\n"), F(f"from typing import {name}\n\nX = 1\n"))
                self.assertEqual(failed, {"bindings"}, _text(p))
                self.assertIn(f"typing.{name} is not in Python 3.12's typing", _text(p))
        # 3.12's own names pass this stage (Self, Buffer: 3.11 and 3.12).
        for imp in ("from typing import Self", "from collections.abc import Buffer"):
            with self.subTest(imp):
                failed, p = _stages(F("X = 1\n"), F(f"{imp}\n\nX = 1\n"))
                self.assertNotIn("bindings", failed, _text(p))
        # Read through the module in a TypeAlias value, evaluated at import.
        failed, p = _stages(F("import typing\n"), F("import typing\n\nX: typing.TypeAlias = typing.ReadOnly\n"))
        self.assertEqual(failed, {"purity"}, _text(p))
        self.assertIn("typing.ReadOnly is not in Python 3.12's typing", _text(p))
        # A builtin that 3.12 lacks.
        failed, p = _stages(F("from typing import TypeAlias\n"),
                            F("from typing import TypeAlias\n\nE: TypeAlias = PythonFinalizationError\n"))
        self.assertEqual(failed, {"purity"}, _text(p))
        # The frozen lists: 3.12's; 3.13 adds `later` to typing.
        import collections.abc
        import typing
        later = {"get_protocol_members", "is_protocol", "NoDefault", "ReadOnly", "TypeIs"}
        self.assertEqual((len(CAO.TYPING_312), len(CAO.ABC_312)), (99, 26))
        self.assertFalse(later & CAO.TYPING_312)
        self.assertLessEqual(CAO.TYPING_312, set(typing.__all__))
        self.assertEqual(set(collections.abc.__all__), CAO.ABC_312)
        if sys.version_info[:2] == (3, 12):
            self.assertEqual(set(typing.__all__), CAO.TYPING_312)
        if sys.version_info[:2] == (3, 13):
            self.assertEqual(set(typing.__all__) - CAO.TYPING_312, later)

    def test_h6_typealias_values_that_run_code(self):
        head = F('''
            from typing import Dict, TypeAlias

            from taxjson.model import Lot

            {}
            ''')
        base = F('''
            from typing import Dict

            from taxjson.model import Lot
            ''')
        cases = [("a lambda", "X: TypeAlias = lambda: 1", "a Lambda"),
                 ("a walrus", "X: TypeAlias = (y := int)", "a NamedExpr"),
                 ("an operator", "X: TypeAlias = Lot + 1", "a BinOp"),
                 ("a project class subscripted", "X: TypeAlias = Lot[int]",
                  "it subscripts Lot, not a typing or collections.abc name"),
                 ("a name bound after the alias", "X: TypeAlias = Later\nLater = int",
                  "Later is not bound before it"),
                 ("a string on a side of |", 'X: TypeAlias = "Lot" | None', "`|` needs a type"),
                 ("a comprehension", "X: TypeAlias = [t for t in (int,)]", "a ListComp"),
                 ("a shadowed builtin generic", "X: TypeAlias = dict[str, int]\ndict = 1",
                  "it subscripts dict")]
        for what, alias, why in cases:
            with self.subTest(what):
                failed, p = _stages(base, head.replace("{}", alias))
                self.assertIn("purity", failed, _text(p))
                self.assertIn(why, _text(p))
        # A name only under TYPE_CHECKING is unbound at import.
        failed, p = _stages(F("from typing import TYPE_CHECKING, Dict\n"),
                            F("from typing import TYPE_CHECKING, Dict, TypeAlias\n\n"
                              "if TYPE_CHECKING:\n    from taxjson.model import Lot\n\n"
                              "X: TypeAlias = Dict[str, Lot]\n"))
        self.assertEqual(failed, {"purity"}, _text(p))
        # Read at run time: the alias's statement is stripped, so a read
        # shows in the ast stage, and the bindings stage names it.
        failed, p = _stages(F("from typing import Dict\n\n\ndef f(v):\n    return isinstance(v, dict)\n"),
                            F("from typing import Dict, TypeAlias\n\nD: TypeAlias = dict\n\n\n"
                              "def f(v):\n    return isinstance(v, D)\n"))
        self.assertEqual(failed, {"ast", "bytecode", "bindings"}, _text(p))
        self.assertIn("the TypeAlias D is read by the module's code at run time", _text(p))


class TestEachStageAlone(unittest.TestCase):
    def test_the_bytecode_stage_compares_what_the_ast_stage_normalizes_away(self):
        # A function's bare `x: T` and its TYPE_CHECKING blocks: gone from
        # both normalized trees, which are equal, and kept in the functions'
        # code, which differs.
        self.assertGreaterEqual(len(BYTECODE_ALONE), 3)
        for what, base, head, _stages_, why in BYTECODE_ALONE:
            with self.subTest(what):
                p = CAO.Pair(REL, base.encode(), head.encode())
                p.run_static()
                self.assertEqual(CAO.dump(p.base_norm), CAO.dump(p.head_norm))
                self.assertEqual(p.problems["ast"], [])
                self.assertIn(why, "\n".join(p.problems["bytecode"]))

    def test_constants_by_type_and_the_future_flag_masked(self):
        p = CAO.Pair(REL, F("def f(v):\n    return v\n").encode(),
                     F("def f(v: int) -> int:\n    return v\n").encode())
        p.run_static()
        self.assertEqual(p.compared, 2)            # f, the module
        # 1 against 1.0 against True: equal in Python, not here.
        for a, b in (("1", "1.0"), ("1", "True"), ("0.0", "-0.0")):
            ca, cb = (compile(f"x = {v}\n", "m", "exec") for v in (a, b))
            self.assertIsNotNone(CAO.code_difference(ca, cb, 0), (a, b))
        # Future mode: CO_FUTURE_ANNOTATIONS is masked out.
        ca = compile("def f(): pass\n", "m", "exec", dont_inherit=True)
        cb = compile("from __future__ import annotations\ndef f(): pass\n", "m", "exec",
                     dont_inherit=True)
        fa, fb = (CAO.code_objects(c, True)[("f", 0)] for c in (ca, cb))
        self.assertIsNotNone(CAO.code_difference(fa, fb, 0))
        self.assertIsNone(CAO.code_difference(fa, fb, CAO.FUTURE_FLAG))

    def test_code_objects_are_matched_by_qualname_and_order(self):
        code = compile("if X:\n    def f(): return 1\nelse:\n    def f(): return 2\n"
                       "g = lambda: (lambda: 3)\nclass K:\n    def m(self): pass\n",
                       "m", "exec", dont_inherit=True)
        self.assertEqual(sorted(CAO.code_objects(code, True)),
                         [("<lambda>", 0), ("<lambda>.<locals>.<lambda>", 0), ("K.m", 0),
                          ("f", 0), ("f", 1)])
        self.assertEqual(sorted(CAO.code_objects(code, False)), [("<module>", 0), ("K", 0)])
        # Two defs swapped: each pairs with the one in its place.
        failed, p = _stages(F("if 1:\n    def f(): return 1\nelse:\n    def f(): return 2\n"),
                            F("if 1:\n    def f(): return 2\nelse:\n    def f(): return 1\n"))
        self.assertEqual(failed, {"ast", "bytecode"}, _text(p))

    def test_the_snapshot_records_what_imports_build(self):
        # A class attribute's value computed at import (an Enum's members,
        # a TypedDict's keys) is recorded, sets in a stable order.
        # B names no special base, so its bare annotation is dropped as a
        # plain class's: the static stages pass, and TypedDict's metaclass
        # makes it a key.
        failed, p = _stages(
            F("from typing import TypedDict\n\n\nclass A(TypedDict):\n    a: int\n\n\n"
              "class B(A):\n    pass\n"),
            F("from typing import TypedDict\n\n\nclass A(TypedDict):\n    a: int\n\n\n"
              "class B(A):\n    b: int\n"))
        self.assertEqual(failed, {"snapshot"}, _text(p))
        # Python 3.13 adds __mutable_keys__, which sorts first: either names B's keys.
        self.assertRegex(_text(p), r"classes\.B\.members\.__(required|mutable)_keys__")
        failed, p = _stages(F("K = {'a', 'b', 'c'}\nS = frozenset(K)\n"),
                            F("K = {'a', 'b', 'c'}\nS = frozenset(K)\n"))
        self.assertEqual(failed, set(), _text(p))

    def test_the_snapshot_records_bases_typealiases(self):
        # It leaves out only the TypeAliases HEAD adds: BASE's is a name like
        # any, so the snapshot alone sees it removed or pointed elsewhere.
        top = "from decimal import Decimal\nfrom typing import TypeAlias\n\n"
        base = F(top + "Num: TypeAlias = Decimal\n")
        for what, head, why in (
                ("removed", F(top), "taxjson.m: names.Num is None in HEAD, ['class', "
                 "'decimal.Decimal'] in BASE"),
                ("pointed elsewhere", F(top + "Num: TypeAlias = float\n"),
                 "names.Num.1 is 'builtins.float' in HEAD, 'decimal.Decimal' in BASE")):
            with self.subTest(what):
                found = _snapshot(CAO.Pair(REL, base.encode(), head.encode()), base, head)
                self.assertEqual(len(found), 1, found)
                self.assertIn(why, found[0])
        p = CAO.Pair(REL, base.encode(), F(top + "Num: TypeAlias = Decimal\nRows: TypeAlias = dict\n")
                     .encode())
        self.assertEqual([a[0] for a in p.new_aliases], ["Rows"])
        self.assertEqual(CAO.skips(p.head_facts, p.head, p.new_aliases)["Rows"], ["any"])
        self.assertNotIn("Num", CAO.skips(p.head_facts, p.head, p.new_aliases))

    def test_explain_prints_the_normalized_module(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "m.py"
            f.write_text(F("from typing import Dict, cast\n\n\ndef f(a: Dict[str, int]) -> int:\n"
                           "    x: int = cast('int', a)\n    return x\n"), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(CAO.main(["--explain", str(f)]), 0)
        text = out.getvalue()
        self.assertIn("the future import present", text.splitlines()[0])
        self.assertNotIn("Dict", text)
        self.assertNotIn("cast", text)
        self.assertIn("Assign(\n", text)


def _change(st, path, om="100644", nm="100644"):
    return (st, path, om, nm, "b-" + path, "h-" + path)


TOML = '''[project]
name = "x"
version = "1.0"

[tool.mypy]
packages = ["taxjson.model", "taxjson.engine"]
strict = true

[[tool.mypy.overrides]]
module = ["taxjson.lib.*"]
follow_imports = "silent"

[[tool.mypy.overrides]]
module = ["taxjson.engine.a", "taxjson.engine.b", "taxjson.engine.c"]
ignore_errors = true
'''


class TestTheFiles(unittest.TestCase):
    def _files(self, changes, blobs=None):
        found, modules, other = CAO.files_problems(changes, lambda o: (blobs or {})[o])
        return found, modules, other

    def test_the_paths_a_typing_commit_may_change(self):
        found, modules, other = self._files([
            _change("M", "src/taxjson/engine/a.py"), _change("M", "src/taxjson/x.py", "100755", "100755"),
            _change("M", "backlog.md"), _change("A", "docs/notes.md", "000000"),
            _change("D", "audits/step10-design/old.md", nm="000000")])
        self.assertEqual(found, [])
        self.assertEqual(modules, ["src/taxjson/engine/a.py", "src/taxjson/x.py"])
        self.assertEqual(other, 3)

    def test_what_the_files_stage_refuses(self):
        cases = [(_change("A", "src/taxjson/new.py", "000000"), "added"),
                 (_change("D", "src/taxjson/old.py", nm="000000"), "deleted"),
                 (_change("M", "src/taxjson/m.py", "100644", "100755"), "its mode changed"),
                 (_change("T", "src/taxjson/m.py", "100644", "120000"), "changed in type"),
                 (_change("M", "tests/test_type_checks.py"), "a shrinking list never needs it"),
                 (_change("M", "tests/test_x.py"), "not a path a typing commit may change"),
                 (_change("M", "scripts/check_annotations_only.py"), "not a path"),
                 (_change("M", "packages/taxjson-fetch/src/taxjson_fetch/x.py"), "not a path"),
                 (_change("M", "src/other/x.py"), "not a path"),
                 (_change("M", "docs/notes.txt"), "not a path"),
                 (_change("M", "docs/link.md", "120000", "120000"), "no regular file"),
                 (_change("A", "pyproject.toml", "000000"), "only its ratchet may change")]
        for change, why in cases:
            with self.subTest(change[1]):
                found, modules, _o = self._files([change])
                self.assertEqual(len(found), 1, found)
                self.assertIn(why, found[0])
                self.assertTrue(found[0].startswith(change[1] + ":1: "), found[0])
                self.assertEqual(modules, [])

    def test_the_ratchet(self):
        shrunk = TOML.replace('"taxjson.engine.a", ', "")
        emptied = TOML.split("[[tool.mypy.overrides]]\nmodule = [\"taxjson.engine.a\"")[0].rstrip() + "\n"
        cases = [("the list shrinks", shrunk, None),
                 ("the table goes, its list empty", emptied, None),
                 ("the list grows", TOML.replace('"taxjson.engine.c"]', '"taxjson.engine.c", "taxjson.engine.d"]'),
                  "the ratchet's module list grew: taxjson.engine.d"),
                 ("a module repeated", TOML.replace('"taxjson.engine.c"]', '"taxjson.engine.c", "taxjson.engine.c"]'),
                  "grew: a repeat"),
                 ("[tool.mypy] packages", TOML.replace('"taxjson.engine"]', '"taxjson.engine", "taxjson.lib"]'),
                  "tool.mypy.packages changed"),
                 ("strict as an integer", TOML.replace("strict = true", "strict = 1"),
                  "tool.mypy.strict changed"),
                 ("another override", shrunk.replace('follow_imports = "silent"', 'follow_imports = "skip"'),
                  "a tool.mypy.overrides table other than the ratchet changed"),
                 ("the ratchet's other keys", shrunk.replace("ignore_errors = true", "ignore_errors = true\nstrict = false"),
                  "beyond its module list"),
                 ("the project", shrunk.replace('version = "1.0"', 'version = "1.1"'),
                  "project.version changed"),
                 ("no parse", TOML + "[[", "does not parse")]
        for what, head, why in cases:
            with self.subTest(what):
                found = CAO.pyproject_problems(TOML.encode(), head.encode())
                if why is None:
                    self.assertEqual(found, [])
                else:
                    self.assertEqual(len(found), 1, found)
                    self.assertIn(why, found[0])
        # A ratchet HEAD adds where BASE had none is refused.
        found = CAO.pyproject_problems(emptied.encode(), TOML.encode())
        self.assertIn("may only shrink BASE's", found[0])
        # Through the files stage.
        found, _m, other = self._files([_change("M", "pyproject.toml")],
                                       {"b-pyproject.toml": TOML.encode(),
                                        "h-pyproject.toml": shrunk.encode()})
        self.assertEqual((found, other), ([], 1))


GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _git(r: Path, *a: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *a], cwd=r, check=True,
                          capture_output=True, text=True, env={**env, **GIT_ID}).stdout


def _commit(r: Path, files: dict, msg: str) -> str:
    _write(r, files)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", msg)
    return _git(r, "rev-parse", "HEAD").strip()


def _repo(r: Path, head_files: dict, base_extra=None):
    """A repository: BASE (the tool, a package, a pyproject, a doc), then HEAD."""
    _git(r, "init", "-q")
    tool = (_paths.REPO / TOOL).read_text(encoding="utf-8")
    base = _commit(r, {TOOL: tool, "src/taxjson/__init__.py": '"""The package."""\n',
                       "src/taxjson/model.py": MODEL, REL: F("def f(a, b=1):\n    return a + b\n"),
                       "pyproject.toml": TOML, "docs/notes.md": "notes\n", **(base_extra or {})},
                   "base")
    return base, (_commit(r, head_files, "head") if head_files else base)


def _base_copy(r: Path, base: str, head: str, *flags: str):
    """The documented route: BASE's copy, from git, run isolated."""
    with tempfile.TemporaryDirectory() as tools:
        tar = subprocess.run(["git", "--no-replace-objects", "archive", base, TOOL], cwd=r,
                             capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tools], input=tar, check=True)
        return subprocess.run([sys.executable, "-I", "-S", str(Path(tools) / TOOL), "--check",
                               base, head, f"--repo={r}", *flags], capture_output=True, text=True)


TYPED = F('''
    from typing import Optional

    from taxjson.model import Lot


    def f(a: Lot, b: Optional[int] = 1) -> Lot:
        return a + b
    ''')


class TestTheCheckOnCommits(unittest.TestCase):
    def test_a_typing_commit_passes_run_as_base_copy(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _repo(r, {REL: TYPED.replace("from taxjson.model import Lot\n\n\n", "\n"),
                                   "pyproject.toml": TOML.replace('"taxjson.engine.a", ', ""),
                                   "docs/notes.md": "more notes\n", "backlog.md": "- TJ-1\n"})
            run = _base_copy(r, base, head)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            lines = run.stdout.splitlines()
            self.assertEqual(lines[-1], "annotations-only: PASS")
            self.assertIn(f"==> Checking HEAD {head[:12]} against BASE {base[:12]}: 4 paths changed",
                          lines[0])
            self.assertIn("==> files: passed, 1 module and 3 other paths", lines)
            self.assertIn(f"==> typing mode: {REL}", lines)
            self.assertIn("==> bytecode: passed, 2 code objects compared", lines)
            self.assertIn("==> snapshot: passed, 1 module imported from each tree", lines)
            self.assertTrue(all(x.startswith("==> ") for x in lines[:-1]), lines)
            # From the checkout: it runs BASE's copy.
            run2 = subprocess.run([sys.executable, str(r / TOOL), "--check", base, head,
                                   f"--repo={r}"], capture_output=True, text=True)
            self.assertEqual((run2.returncode, run2.stdout), (0, run.stdout))

    def test_the_snapshot_imports_the_temporary_tree(self):
        # A module that exists only in the temporary tree: imported from it,
        # not from the dev clone the venv's editable install names.
        only = "src/taxjson/only_in_this_tree_5d1c.py"
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _repo(r, {only: F("def g(x: int) -> int:\n    return x\n")},
                               {only: F("def g(x):\n    return x\n")})
            run = _base_copy(r, base, head)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("==> snapshot: passed, 1 module imported from each tree", run.stdout)

    def test_a_refusal_fails_and_runs_none_of_heads_code(self):
        marker = "ran-7c2e.txt"
        evil = F(f"open({marker!r}, 'w').write('ran')\n\n\ndef f(a: int, b: int = 1):\n    return a + b\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _repo(r, {REL: evil})
            run = _base_copy(r, base, head)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            lines = run.stdout.splitlines()
            self.assertEqual(lines[-1], "annotations-only: FAIL (ast, bytecode)")
            self.assertIn("==> ast: FAILED, 1 refusal(s)", lines)
            self.assertIn(f"- {REL}:4: HEAD adds `open('{marker}', 'w').write('ran')`", lines)
            self.assertIn("==> snapshot: not run: HEAD's code is run only once the stages "
                          "before it pass", lines)
            self.assertFalse(list(Path(td).rglob(marker)))

    def test_heads_own_copy_of_the_tool_is_not_what_runs(self):
        # HEAD rewrites the tool to pass everything: BASE's copy runs, and
        # refuses the path.
        fake = "import sys\nprint('annotations-only: PASS')\nsys.exit(0)\n"
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _repo(r, {TOOL: fake, REL: F("def f(a, b=2):\n    return a + b\n")})
            run = _base_copy(r, base, head)
            self.assertEqual(run.returncode, 1, run.stdout)
            self.assertIn(f"- {TOOL}:1: not a path a typing commit may change", run.stdout)
            self.assertEqual(run.stdout.splitlines()[-1],
                             "annotations-only: FAIL (files, ast, bytecode)")

    def test_a_future_only_commit_then_a_typing_commit(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            plain = P("def f(a, b=1):\n    return a + b\n")
            base, _h = _repo(r, {}, {REL: plain})
            # The future import and the annotations in one commit (beside
            # the branch: HEAD does not move).
            _write(r, {REL: TYPED})
            _git(r, "add", "-A")
            both = _git(r, "commit-tree", _git(r, "write-tree").strip(), "-p", base,
                        "-m", "both").strip()
            run = _base_copy(r, base, both)
            self.assertEqual(run.returncode, 1)
            self.assertIn("add the future import first, in its own commit", run.stdout)
            future = _commit(r, {REL: F("def f(a, b=1):\n    return a + b\n")}, "future")
            typed = _commit(r, {REL: TYPED.replace("from taxjson.model import Lot\n\n\n", "\n")},
                            "typed")
            for b, h, mode in ((base, future, "future"), (future, typed, "typing")):
                run = _base_copy(r, b, h)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn(f"==> {mode} mode: {REL}", run.stdout)

    def test_what_names_nothing_to_check(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _git(r, "init", "-q")
            old = _commit(r, {"README.md": "x\n"}, "old")
            base = _commit(r, {TOOL: (_paths.REPO / TOOL).read_text(encoding="utf-8")}, "tool")
            run = _base_copy(r, base, old)
            self.assertEqual(run.returncode, 2)
            self.assertIn("is not an ancestor of HEAD", run.stderr)
            run = subprocess.run([sys.executable, "-I", "-S", str(_paths.REPO / TOOL), "--check",
                                  old, base, f"--repo={r}"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn(f"Error: BASE lacks {TOOL}: the tool lands in a commit of its own",
                          run.stderr)
            run = _base_copy(r, base, "nope")
            self.assertEqual(run.returncode, 2)
            self.assertIn("'nope' is no commit", run.stderr)
            # Trees, with --trees: no ancestry.
            t_base, t_old = (_git(r, "rev-parse", f"{c}^{{tree}}").strip() for c in (base, old))
            run = _base_copy(r, t_base, t_old, "--trees")
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn(f"- {TOOL}:1: not a path a typing commit may change", run.stdout)


class TestTheArguments(unittest.TestCase):
    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CAO.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_usage(self):
        for argv in ([], ["--check", "A"], ["--check", "A", "B", "C"], ["--explain"],
                     ["--explain", "a", "b"], ["--check", "--explain", "x"], ["A", "B"]):
            with self.subTest(argv):
                rc, _o, err = self._main(argv)
                self.assertEqual(rc, 2)
                self.assertIn("Usage: scripts/check_annotations_only.py --check BASE HEAD", err)

    def test_an_older_python_is_refused(self):
        with mock.patch.object(CAO.sys, "version_info", (3, 11, 9, "final", 0)):
            rc, _o, err = self._main(["--check", "A", "B"])
        self.assertEqual(rc, 2)
        self.assertIn("needs Python 3.12 or later, this is 3.11", err)


if __name__ == "__main__":
    unittest.main()

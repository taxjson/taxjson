"""scripts/move_defs.py (PLAN.md step 10 B0a): a module's top-level
definitions move whole into new modules, on a synthetic repository
(src/pkg/core.py into src/pkg/eng/, ca/ and us/ below it).

- A batch's statements move with their decorators, the comments above
  them and the stores into them; each new module gets its docstring, the
  source's imports the moved code reads (string annotations too) in the
  source's spelling, the batch's earlier modules' imports, then the
  statements byte for byte; the source imports them back, lists them in
  `__all__` and drops what only they read; a second batch grows the
  table; line ends are kept; mutation_audit's rows and the knowledge
  pack's references follow, what the tool cannot place is listed; the
  baseline is the repository's check_architecture's record, and its
  failure undoes the move.
- Each refusal, before anything is written.
- --check: BASE's tool, run on BASE with HEAD's table, gives HEAD's tree;
  anything else fails and runs none of HEAD's code; the later checks
  (pins, AST, bindings, runtime, layers) each catch what a tool bug would
  write; BASE's copy is what runs; the arguments."""
import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _paths


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", _paths.REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MD = _load("move_defs")

CORE = '''from pkg import helper as _H
import functools
from dataclasses import dataclass, field as _dc_field
from typing import List, Optional

from pkg.model import Row
from pkg.model import Item as Item  # re-export

# The money constants, read once.
_ZERO = 0
_ONE = 1


def _shown(x):
    """A figure as shown."""
    return _H.fmt(x) if x else _ZERO


@functools.lru_cache(maxsize=64)
def _ordinal(s: str) -> int:
    return int(s.replace("-", ""))


# A candidate pair.
@dataclass(frozen=True)
class _Cand:
    key: str
    size: int = 1
    rows: List[str] = _dc_field(default_factory=list)

    def first(self, rows: "Optional[Row]" = None) -> "Row":
        return rows or Row(self.key, _ZERO)


_Cand.__doc__ = """One candidate."""


def kept(x):
    return _shown(x) + str(_ordinal("2024-01-01") + _ONE)
'''
HELPER = 'def fmt(x):\n    return f"{x:.2f}"\n'
MODEL = ('class Row:\n    def __init__(self, key, size=0):\n        self.key, self.size = key, size\n'
         '\n\nclass Item:\n    pass\n')
TABLE = '''# the synthetic batch
src/pkg/core.py src/pkg/eng/__init__.py The engines
src/pkg/core.py src/pkg/eng/consts.py The constants
    _ZERO _ONE _shown
src/pkg/core.py src/pkg/eng/ca/__init__.py The Canadian engine
src/pkg/core.py src/pkg/eng/ca/cand.py The candidates
    _ordinal
    _Cand
'''
CORE_AFTER = '''from pkg.model import Item as Item  # re-export
from pkg.eng.consts import _ZERO, _ONE, _shown
from pkg.eng.ca.cand import _ordinal, _Cand

# What moved out of this module, re-exported by name (PLAN step 10).
__all__ = ["_ZERO", "_ONE", "_shown", "_ordinal", "_Cand"]


def kept(x):
    return _shown(x) + str(_ordinal("2024-01-01") + _ONE)
'''
CONSTS = '''"""The constants.

Moved from core.py (PLAN step 10).
"""
from pkg import helper as _H


# The money constants, read once.
_ZERO = 0
_ONE = 1


def _shown(x):
    """A figure as shown."""
    return _H.fmt(x) if x else _ZERO
'''
CAND = '''"""The candidates.

Moved from core.py (PLAN step 10).
"""
import functools
from dataclasses import dataclass, field as _dc_field
from typing import List, Optional

from pkg.model import Row

from pkg.eng.consts import _ZERO


@functools.lru_cache(maxsize=64)
def _ordinal(s: str) -> int:
    return int(s.replace("-", ""))


# A candidate pair.
@dataclass(frozen=True)
class _Cand:
    key: str
    size: int = 1
    rows: List[str] = _dc_field(default_factory=list)

    def first(self, rows: "Optional[Row]" = None) -> "Row":
        return rows or Row(self.key, _ZERO)


_Cand.__doc__ = """One candidate."""
'''
AUDIT = ('TARGET_FUNCS = [\n    ("src/pkg/core.py", "_Cand.first"),\n'
         '    ("src/pkg/core.py", "kept"),\n    ("src/pkg/other.py", "_shown"),\n'
         "    ('src/pkg/core.py', '_shown'),\n]\n")
WRITTEN = ("src/pkg/core.py", "src/pkg/eng/__init__.py", "src/pkg/eng/consts.py",
           "src/pkg/eng/ca/__init__.py", "src/pkg/eng/ca/cand.py")

# check_architecture, as a stand-in: a src file's count of `return ` is its
# E741 hits, which move with the code; it fails when they differ from the
# baseline, and records them with the tool's --accept-growth.
STAND_IN = (
    '"""A stand-in for check_architecture."""\n'
    'import json\nimport sys\nfrom pathlib import Path\n\n'
    'ROOT = Path(__file__).resolve().parent.parent\n'
    'B = ROOT / "scripts" / "architecture-baseline.json"\n'
    'counts = {f.relative_to(ROOT).as_posix(): f.read_text().count("return ")\n'
    '          for f in sorted((ROOT / "src").rglob("*.py")) if "return " in f.read_text()}\n'
    'doc = json.loads(B.read_text())\n'
    f'if {MD.ACCEPT!r} in sys.argv:\n'
    '    doc["ruff"] = {"E741": counts}\n'
    '    B.write_text(json.dumps(doc, indent=1) + "\\n")\n'
    'sys.exit(0 if doc["ruff"].get("E741") == counts else 1)\n')
BASELINE = json.dumps({"ruff": {"E741": {"src/pkg/core.py": 4, "src/pkg/helper.py": 1}}},
                      indent=1) + "\n"


def _git(r: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=r, check=True, capture_output=True,
                          text=True).stdout


def _write(r: Path, rel: str, text: str) -> None:
    (r / rel).parent.mkdir(parents=True, exist_ok=True)
    with open(r / rel, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _repo(r: Path, files=None, tools=False) -> None:
    files = {"src/pkg/__init__.py": "", "src/pkg/helper.py": HELPER, "src/pkg/model.py": MODEL,
             "src/pkg/core.py": CORE, **(files or {})}
    for rel, text in files.items():
        if text is not None:
            _write(r, rel, text)
    if tools:
        for t in MD.TOOLS:
            (r / t).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_paths.REPO / t, r / t)


def _run(r: Path, table=TABLE, dry_run=False):
    _write(r, MD.TABLE, table)
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        try:
            rc = MD.move(r, r / MD.TABLE, dry_run)
        except MD.Refused as e:
            print(f"refused: {e}")
            rc = 1
    return rc, out.getvalue()


def _files(r: Path) -> dict:
    return {p.relative_to(r).as_posix(): p.read_bytes() for p in sorted(r.rglob("*"))
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts}


def _child(r: Path, code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-B", "-c",
                           f"import sys; sys.path.insert(0, {str(r / 'src')!r})\n{code}"],
                          capture_output=True, text=True)


class TestTheMove(unittest.TestCase):
    def test_a_batch_on_a_synthetic_repository(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            for rel, want in (("src/pkg/core.py", CORE_AFTER), ("src/pkg/eng/consts.py", CONSTS),
                              ("src/pkg/eng/ca/cand.py", CAND),
                              ("src/pkg/eng/__init__.py", '"""The engines."""\n'),
                              ("src/pkg/eng/ca/__init__.py", '"""The Canadian engine."""\n')):
                self.assertEqual((r / rel).read_text(), want, rel)
            self.assertIn("src/pkg/core.py -> src/pkg/eng/ca/cand.py: 2 name(s) in 3 statement(s)",
                          out)
            self.assertIn("from the batch: pkg.eng.consts", out)
            self.assertIn("drops List, Optional, Row, _H, _dc_field, dataclass, functools", out)
            self.assertIn("the architecture baseline not recorded (no check_architecture)", out)
            # The moved code runs, and the source's names are its modules' objects.
            run = _child(r, "import pkg.core as c, pkg.eng.ca.cand as k, pkg.eng.consts as z\n"
                            "assert c._Cand is k._Cand and c._ZERO is z._ZERO\n"
                            "assert c._ordinal.cache_parameters() == {'maxsize': 64, 'typed': False}\n"
                            "assert k._Cand.__doc__ == 'One candidate.'\n"
                            "assert k._Cand('a').first().key == 'a'\n"
                            "print(c.kept(2))")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, "2.0020240102\n")

    def test_a_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            _write(r, MD.TABLE, TABLE)
            before = _files(r)
            rc, out = _run(r, dry_run=True)
            self.assertEqual(rc, 0, out)
            self.assertIn("2 name(s) in 3 statement(s)", out)
            self.assertEqual(_files(r), before)

    def test_line_ends_are_kept(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": CORE.replace("\n", "\r\n")})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            for rel, want in (("src/pkg/core.py", CORE_AFTER), ("src/pkg/eng/ca/cand.py", CAND),
                              ("src/pkg/eng/__init__.py", '"""The engines."""\n')):
                with open(r / rel, encoding="utf-8", newline="") as h:
                    self.assertEqual(h.read(), want.replace("\n", "\r\n"), rel)

    def test_the_sources_end_is_kept(self):
        for what, end in (("blank lines", "\n\n\n"), ("no newline", "")):
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, {"src/pkg/core.py": CORE.rstrip("\n") + end})
                rc, out = _run(r)
                self.assertEqual(rc, 0, out)
                self.assertEqual((r / "src/pkg/core.py").read_text(),
                                 CORE_AFTER.rstrip("\n") + end)
        # A cut at the end takes the blank lines along.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": CORE.replace("\n\ndef kept", "\n\nkept = 1\n\n\ndef kept2")
                      .replace("def kept2(x):", "def _last(x):") + "\n\n"})
            rc, out = _run(r, TABLE + "    _last\n")
            self.assertEqual(rc, 0, out)
            self.assertTrue((r / "src/pkg/core.py").read_text().endswith("\nkept = 1\n"))

    def test_a_second_batch_grows_the_table(self):
        first = TABLE.split("src/pkg/core.py src/pkg/eng/ca/__init__.py")[0]
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            self.assertEqual(_run(r, first)[0], 0)
            self.assertIn('__all__ = ["_ZERO", "_ONE", "_shown"]\n', (r / "src/pkg/core.py").read_text())
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertEqual((r / "src/pkg/core.py").read_text(), CORE_AFTER)
            # The source's import of an earlier batch's names, as it is spelled.
            self.assertEqual((r / "src/pkg/eng/ca/cand.py").read_text(),
                             CAND.replace("from pkg.model import Row\n\nfrom pkg.eng.consts",
                                          "from pkg.model import Row\nfrom pkg.eng.consts"))
            self.assertNotIn("eng/consts.py", out)
            # Every row done: no batch.
            rc, out = _run(r)
            self.assertEqual(rc, 1)
            self.assertIn("every row of the table is done", out)

    def test_mutation_audit_rows_follow(self):
        audit = AUDIT
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"scripts/mutation_audit.py": audit})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertEqual((r / "scripts/mutation_audit.py").read_text(), audit.replace(
                '"src/pkg/core.py", "_Cand', '"src/pkg/eng/ca/cand.py", "_Cand').replace(
                "'src/pkg/core.py', '_shown'", "'src/pkg/eng/consts.py', '_shown'"))
            self.assertIn("2 TARGET_FUNCS row(s) retargeted", out)

    def test_the_knowledge_pack_follows(self):
        doc = ("- `src/pkg/core.py` — `_shown`, `_ZERO`: the figures.\n"
               "- `src/pkg/core.py` — `kept`, `_shown` and `_Cand.first`: split.\n"
               "- `src/pkg/core.py` — `_Cand`, `first`: a method follows its class.\n"
               "- `src/pkg/core.py` — `kept` (it calls `_shown`): by hand.\n"
               "- `src/pkg/core.py` — `first`: a method alone, by hand.\n"
               "- the readers (`src/pkg/core.py` `_ordinal`, `src/pkg/core.py` `kept`)\n"
               "- `src/pkg/core.py:_ordinal` and `src/pkg/core.py:kept`.\n"
               "- `src/pkg/core.py` — `kept`, `Item`: unchanged.\n")
        want = ("- `src/pkg/eng/consts.py` — `_shown`, `_ZERO`: the figures.\n"
                "- `src/pkg/core.py` — `kept`; `src/pkg/eng/consts.py` — `_shown`; "
                "`src/pkg/eng/ca/cand.py` — `_Cand.first`: split.\n"
                "- `src/pkg/eng/ca/cand.py` — `_Cand`, `first`: a method follows its class.\n"
                "- `src/pkg/core.py` — `kept` (it calls `_shown`): by hand.\n"
                "- `src/pkg/core.py` — `first`: a method alone, by hand.\n"
                "- the readers (`src/pkg/eng/ca/cand.py` `_ordinal`, `src/pkg/core.py` `kept`)\n"
                "- `src/pkg/eng/ca/cand.py:_ordinal` and `src/pkg/core.py:kept`.\n"
                "- `src/pkg/core.py` — `kept`, `Item`: unchanged.\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"docs/map.md": doc, "AGENTS.md": doc.replace("\n", "\r\n"),
                      "docs/sub/other.md": doc})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertEqual((r / "docs/map.md").read_text(), want)
            with open(r / "AGENTS.md", encoding="utf-8", newline="") as h:
                self.assertEqual(h.read(), want.replace("\n", "\r\n"))
            self.assertEqual((r / "docs/sub/other.md").read_text(), doc)   # not the pack
            self.assertIn("10 reference(s) rewritten in 2 file(s); 4 to edit by hand:", out)
            self.assertIn("docs/map.md:4: `src/pkg/core.py` — `kept`", out)
            self.assertIn("docs/map.md:5: `src/pkg/core.py` — `first`", out)

    def test_the_baseline_is_check_architectures_record(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {MD.ARCH: STAND_IN, "scripts/architecture-baseline.json": BASELINE})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertIn("the architecture baseline recorded", out)
            got = json.loads((r / "scripts/architecture-baseline.json").read_text())
            self.assertEqual(got["ruff"]["E741"], {"src/pkg/core.py": 1,
                                                   "src/pkg/eng/ca/cand.py": 2,
                                                   "src/pkg/eng/consts.py": 1,
                                                   "src/pkg/helper.py": 1})
        # Its failure undoes everything, the baseline too.
        fail = STAND_IN.replace("sys.exit(0 if", "print('the stand-in fails')\nsys.exit(3 if")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {MD.ARCH: fail, "scripts/architecture-baseline.json": BASELINE,
                      "docs/map.md": "`src/pkg/core.py` — `_shown`\n"})
            _write(r, MD.TABLE, TABLE)
            before = _files(r)
            rc, out = _run(r)
            self.assertEqual(rc, 1)
            self.assertIn(f"check_architecture {MD.ACCEPT} fails", out)
            self.assertIn("the stand-in fails", out)
            self.assertIn("every change undone", out)
            self.assertEqual(_files(r), before)
            self.assertFalse((r / "src/pkg/eng").exists())

    def test_what_is_no_refusal(self):
        cases = [
            # A builtin, and a name of a module the source imports plainly.
            ("a builtin", CORE.replace('return _H.fmt(x) if x', 'return _H.fmt(len(str(x))) if x'),
             TABLE),
            # A `noqa: F401` import that code staying reads too: it stays.
            ("a noqa: F401 import kept code reads",
             CORE.replace("from pkg import helper as _H\n",
                          "from pkg import helper as _H  # noqa: F401\n").replace(
                 "return _shown(x) + str(", "return _H.fmt(x) + _shown(x) + str("), TABLE),
            # A shared module that picks an engine (JOINS) reads both.
            ("by_country", "def a():\n    return 1\n\n\ndef b():\n    return 2\n\n\n"
                           "def pick():\n    return a() + b()\n",
             "src/pkg/core.py src/pkg/eng/__init__.py E\n"
             "src/pkg/core.py src/pkg/eng/ca/__init__.py C\n"
             "src/pkg/core.py src/pkg/eng/ca/x.py X\n    a\n"
             "src/pkg/core.py src/pkg/eng/us/__init__.py U\n"
             "src/pkg/core.py src/pkg/eng/us/y.py Y\n    b\n"
             "src/pkg/core.py src/pkg/eng/by_country.py P\n    pick\n"),
        ]
        for what, core, table in cases:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, {"src/pkg/core.py": core})
                rc, out = _run(r, table)
                self.assertEqual(rc, 0, out)


def _swap(*pairs):
    """An edit of CORE: each (old, new) pair of PAIRS replaced once."""
    def edit(core):
        for old, new in zip(pairs[::2], pairs[1::2]):
            assert old in core, old
            core = core.replace(old, new, 1)
        return core
    return edit


TINY = "def a():\n    return 1\n\n\ndef b():\n    return a()\n"
LAYER = ("src/pkg/core.py src/pkg/eng/__init__.py E\n"
         "src/pkg/core.py src/pkg/eng/ca/__init__.py C\n"
         "src/pkg/core.py src/pkg/eng/us/__init__.py U\n")
REFUSALS = [
    # (what, the source or an edit of CORE, the table or None, other files, the message)
    ("no top-level def", None, TABLE.replace("    _ZERO _ONE", "    _ZERO _NOPE _ONE"), {},
     "_NOPE is no top-level def, class or assignment"),
    ("a bare annotation", _swap("_ONE = 1\n", "_ONE = 1\n_TWO: int\n"),
     TABLE.replace("    _ZERO _ONE", "    _ZERO _TWO _ONE"), {}, "_TWO is no top-level def"),
    ("bound twice", _swap("\n\ndef kept", "\n\n_ONE = 2\n\n\ndef kept"), None, {},
     "_ONE is bound at module level twice"),
    ("bound by an import too", _swap("from pkg.model import Row\n",
                                     "from pkg.model import Row\nfrom pkg.helper import fmt as _shown\n"),
     None, {}, "_shown is bound at module level also by an import"),
    ("bound through global", _swap("\n\ndef kept", "\n\ndef bump():\n    global _ONE\n    _ONE = 2\n"
                                                    "\n\ndef kept"),
     None, {}, "_ONE is bound at module level also through `global`"),
    ("bound in the main guard", _swap("\n\ndef kept", "\n\nif __name__ == '__main__':\n    _ONE = 3\n"
                                                      "\n\ndef kept"),
     None, {}, "_ONE is bound at module level also at line"),
    ("a statement with a name that stays", _swap("_ZERO = 0\n", "_ZERO = _NIL = 0\n"), None, {},
     "binds _NIL with _ZERO"),
    ("a shared line", _swap("_ZERO = 0\n", "_ZERO = 0; _NIL = 1\n"), None, {},
     "holds a statement that moves and another"),
    ("a store into a moved and a kept name", _swap('_Cand.__doc__ = """One candidate."""',
                                                   '_Cand.__doc__ = kept.__doc__ = "x"'),
     None, {}, "stores into _Cand, kept: one moves"),
    ("a name that stays", _swap("return _H.fmt(x) if x else _ZERO",
                                "return _H.fmt(x) if x else kept(0)"), None, {},
     "reads kept, which stays in src/pkg/core.py"),
    ("a name a later row takes", _swap("return _H.fmt(x) if x else _ZERO",
                                       "return _H.fmt(x) if x else _ordinal('1')"), None, {},
     "would import _ordinal from src/pkg/eng/ca/cand.py, which comes later in the table"),
    ("ca reads us", TINY, LAYER + "src/pkg/core.py src/pkg/eng/us/x.py X\n    a\n"
                                  "src/pkg/core.py src/pkg/eng/ca/y.py Y\n    b\n", {},
     "pkg.eng.ca.y imports pkg.eng.us.x: one country's engine never reads the other's"),
    ("us reads ca", TINY, LAYER + "src/pkg/core.py src/pkg/eng/ca/x.py X\n    a\n"
                                  "src/pkg/core.py src/pkg/eng/us/y.py Y\n    b\n", {},
     "pkg.eng.us.y imports pkg.eng.ca.x: one country's engine never reads the other's"),
    ("shared reads ca", TINY, LAYER + "src/pkg/core.py src/pkg/eng/ca/x.py X\n    a\n"
                                      "src/pkg/core.py src/pkg/eng/y.py Y\n    b\n", {},
     "pkg.eng.y imports pkg.eng.ca.x: a shared module reads no country's engine"),
    ("shared imports ca", "from pkg.eng.ca import x\n\n\ndef b():\n    return x\n",
     "src/pkg/core.py src/pkg/eng/y.py Y\n    b\n",
     {"src/pkg/eng/__init__.py": "", "src/pkg/eng/ca/__init__.py": "",
      "src/pkg/eng/ca/x.py": ""}, "pkg.eng.y imports pkg.eng.ca: a shared module reads"),
    ("__file__", _swap("return _H.fmt(x)", "return __file__ + _H.fmt(x)"), None, {},
     "reads its own module or where it runs (__file__)"),
    ("__name__", _swap("return _H.fmt(x)", "return __name__ + _H.fmt(x)"), None, {},
     "(__name__)"),
    ("globals()", _swap("return _H.fmt(x)", "return globals() and _H.fmt(x)"), None, {},
     "(globals"),
    ("vars()", _swap("return _H.fmt(x)", "return vars() and _H.fmt(x)"), None, {}, "(vars()"),
    ("sys.modules, as an alias", _swap("import functools\n", "import functools\nimport sys as _s\n",
                                       "return _H.fmt(x)", "return _s.modules['pkg.core'] and _H.fmt(x)"),
     None, {}, "sys.modules[__name__]"),
    ("code at import", _swap("\n\ndef kept", "\n\nprint('x')\n\n\ndef kept"), None, {},
     "runs code at import (Expr)"),
    ("a dropped import another module imports", None, None,
     {"src/pkg/user.py": "def f():\n    from pkg.core import functools\n    return functools\n"},
     "src/pkg/user.py:2 imports functools from pkg.core, which no longer reads it"),
    ("an import that loses a name and its comment",
     _swap("from typing import List, Optional\n", "from typing import List, Optional  # the types\n",
           "def kept(x):", "def kept(x) -> List[str]:"),
     None, {}, "the import loses Optional and carries a comment"),
    ("a relative import", _swap("from pkg import helper as _H\n", "from . import helper as _H\n"),
     None, {}, "the moved code needs a relative import"),
    ("no package row", None, TABLE.replace("src/pkg/core.py src/pkg/eng/__init__.py The engines\n", ""),
     {}, "src/pkg/eng/ is no package, and no row makes it one"),
    ("a package row's folder exists", None, None, {"src/pkg/eng/notes.txt": "x\n"},
     "src/pkg/eng/ exists: a package row makes a new folder"),
    ("a done row that does not hold", None, None,
     {"src/pkg/eng/__init__.py": "", "src/pkg/eng/consts.py": ""},
     "is done (src/pkg/eng/consts.py exists), but src/pkg/core.py defines or does not import "
     "_ONE, _ZERO, _shown"),
    ("a done row below one that is not", None,
     "src/pkg/core.py src/pkg/eng/x.py X\n    kept\nsrc/pkg/core.py src/pkg/eng/__init__.py E\n",
     {"src/pkg/eng/__init__.py": ""}, "is done (src/pkg/eng/__init__.py exists) but line 1"),
    ("an `__all__` that is no list", _swap("\n\ndef kept", "\n\n__all__ = list('ab')\n\n\ndef kept"),
     None, {}, "`__all__` is no single list of names"),
    ("a global statement", _swap("def _shown(x):\n", "def _shown(x):\n    global _LAZY\n    _LAZY = 1\n"),
     None, {}, "declares `global _LAZY`: moved, it would bind src/pkg/eng/consts.py's"),
    ("an import twice", _swap("from pkg.model import Row\n", "from pkg.model import Row\n"
                              "from pkg.helper import fmt as Row  # noqa: F811\n"),
     None, {}, "reads Row, which src/pkg/core.py binds more than once (line 6, 7)"),
    ("an import below", _swap("\n\n" "@functools.lru_cache", "\n\nfrom pkg.helper import fmt as _late  "
                              "# noqa: E402\n\n\n" "@functools.lru_cache",
                              "return _H.fmt(x) if x", "return _late(x) if x"),
     None, {}, "reads _late, which src/pkg/core.py imports below it (line 19)"),
    ("a noqa: F401 import only moved code reads",
     _swap("from pkg import helper as _H\n", "from pkg import helper as _H  # noqa: F401\n"),
     None, {}, "src/pkg/core.py:1: _H, which only the moved code reads, is marked `noqa: F401`"),
    ("a noqa: F401 on an import's first line",
     _swap("from typing import List, Optional\n",
           "from typing import (  # noqa: F401\n    List,\n    Optional,\n)\n"),
     None, {}, "src/pkg/core.py:4: List, which only the moved code reads, is marked `noqa: F401`"),
    ("a NOQA: F401 in capitals",
     _swap("from pkg import helper as _H\n", "from pkg import helper as _H  # NOQA:F401\n"),
     None, {}, "src/pkg/core.py:1: _H, which only the moved code reads, is marked `noqa: F401`"),
    ("a bare noqa", _swap("from pkg import helper as _H\n", "from pkg import helper as _H  # noqa\n"),
     None, {}, "src/pkg/core.py:1: _H, which only the moved code reads, is marked `noqa: F401` "
               "(or a bare `noqa`)"),
    ("a star import", _swap("import functools\n", "import functools\nfrom os.path import *  # noqa\n"),
     None, {}, "src/pkg/core.py:3: a star import"),
    ("a bare __module__", _swap("    key: str\n", "    key: str\n    KEY = __module__ + '.C'\n"),
     None, {}, "__module__): moved, it would read src/pkg/eng/ca/cand.py's"),
    ("a cycle", _swap("import functools\n", "import functools\nimport pkg.core as _me\n",
                      "return _H.fmt(x) if x", "return _H.fmt(x) if x and _me"),
     None, {}, "pkg.eng.consts imports pkg.core, which imports pkg.eng.consts back: a cycle"),
    # The table.
    ("a line that is no row", None, "src/pkg/core.py src/pkg/eng/x.py\n", {},
     "want `SOURCE TARGET DESCRIPTION`"),
    ("names before a row", None, "    kept\n", {}, "names before any row"),
    ("a name twice", None, TABLE.replace("    _ordinal\n", "    _ordinal _ZERO\n"), {},
     "the table names _ZERO twice"),
    ("a target twice", None, TABLE + "src/pkg/core.py src/pkg/eng/consts.py X\n    kept\n", {},
     "names a target twice: src/pkg/eng/consts.py"),
    ("a source as a target", None, "src/pkg/core.py src/pkg/eng/x.py X\n    kept\n"
                                   "src/pkg/eng/x.py src/pkg/core.py Y\n    kept\n", {},
     "moves names into a source"),
    ("a package row with names", None, "src/pkg/core.py src/pkg/eng/__init__.py E\n    kept\n", {},
     "a package row (src/pkg/eng/__init__.py) takes no names"),
    ("a module row without", None, "src/pkg/core.py src/pkg/eng/x.py X\n", {}, "takes no names"),
    ("a path outside src", None, "lib/core.py src/pkg/eng/x.py X\n    kept\n", {},
     "is no path to a module below src/<package>/"),
    ("a dunder", None, "src/pkg/core.py src/pkg/eng/x.py X\n    __all__\n", {},
     "is no name a row can take"),
    ("a docstring quote", None, 'src/pkg/core.py src/pkg/eng/x.py X """\n    kept\n', {},
     "a description without"),
]


class TestRefusals(unittest.TestCase):
    def test_each_is_refused_before_anything_is_written(self):
        for what, core, table, files, why in REFUSALS:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                src = core if isinstance(core, str) else (core(CORE) if core else CORE)
                _repo(r, {"src/pkg/core.py": src, **files})
                _write(r, MD.TABLE, table or TABLE)
                before = _files(r)
                rc, out = _run(r, table or TABLE)
                self.assertEqual(rc, 1, out)
                self.assertIn(why, out)
                self.assertEqual(_files(r), before)

    def test_a_path_git_ignores(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {".gitignore": "eng/\n"})
            _git(r, "init", "-q")
            rc, out = _run(r)
            self.assertEqual(rc, 1)
            self.assertIn("src/pkg/eng/__init__.py is a path git ignores", out)

    def test_the_patches_through_the_source_are_listed(self):
        user = ("import pkg.core as core\nfrom unittest import mock\n\n\n"
                "def f():\n    core._shown = None\n    mock.patch.object(core, 'functools')\n"
                "    return mock.patch('pkg.core._Cand'), mock.patch('pkg.core.kept')\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            # TARGET_FUNCS rows the move retargets are no seam it lists.
            _repo(r, {"tests/test_user.py": user, "scripts/mutation_audit.py": AUDIT})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertIn("move_defs: 3 seam(s) through a source, which --check's seams stage "
                          "will fail:\n", out)
            why = ": pkg.core no longer defines it: the code there reads its own binding\n"
            for x in ("tests/test_user.py:6: stores into pkg.core._shown (src/pkg/eng/consts.py "
                      "binds it)",
                      "tests/test_user.py:7: patches pkg.core.functools (src/pkg/eng/ca/cand.py "
                      "binds it)",
                      "tests/test_user.py:8: names pkg.core._Cand (src/pkg/eng/ca/cand.py binds "
                      "_Cand)"):
                self.assertIn(f"  {x}{why}", out)
            self.assertNotIn("pkg.core.kept", out)
            self.assertNotIn("  scripts/mutation_audit.py:", out)


class TestTheReading(unittest.TestCase):
    def test_string_annotations_are_read(self):
        f = ast.parse('def f(a: "Optional[Row]", *b: "X") -> "List[Y]":\n    z: "W" = 1\n').body[0]
        self.assertEqual(MD.annotation_names(f), {"Optional", "Row", "X", "List", "Y", "W"})
        self.assertEqual(MD.reads(f), {"Optional", "Row", "X", "List", "Y", "W"})

    def test_the_stores(self):
        for code, want in (("X.a = 1", {"X"}), ("X[k] += 1", {"X"}), ("del X.a", {"X"}),
                           ("X.a: int = 1", {"X"}), ("X = 1", None), ("X.a = Y = 1", None)):
            self.assertEqual(MD.store_roots(ast.parse(code).body[0]), want, code)

    def test_the_country_rule(self):
        p = MD.layer_problem
        self.assertIsNone(p("t.engine.ca.x", "t.engine.ca.y"))
        self.assertIsNone(p("t.engine.ca.x", "t.engine.consts"))
        self.assertIsNone(p("t.engine.ca.x", "t.lib.us"))
        self.assertIsNone(p("t.engine.by_country", "t.engine.us.taxrules"))
        self.assertIsNone(p("t.engine.consts", "t.lib.core"))
        self.assertIn("never reads", p("t.engine.us.x", "t.engine.ca"))
        self.assertIn("shared module", p("t.engine.consts", "t.engine.us.x"))

    def test_the_cycle_rule(self):
        rows = MD.read_table("src/t/lib/core.py src/t/e/a.py A\n    x\nsrc/t/lib/core.py src/t/e/b.py B\n"
                             "    y\nsrc/t/lib/country.py src/t/r/country.py C\n    z\n")
        graph = {"t.lib.core": {"t.e.a", "t.e.b", "t.lib.country"}, "t.e.a": {"t.lib.country"},
                 "t.e.b": set(), "t.lib.country": {"t.r.country"}, "t.r.country": set()}
        # A target importing a source that does not import it back: no cycle.
        self.assertEqual(MD.cycle_problems(graph, rows), [])
        graph["t.e.a"].add("t.lib.core")
        self.assertEqual(MD.cycle_problems(graph, rows),
                         ["t.e.a imports t.lib.core, which imports t.e.a back: a cycle"])
        graph["t.e.a"].discard("t.lib.core")
        graph["t.lib.core"] = {"t.e.a"}
        graph["t.e.a"].add("t.e.b")
        graph["t.e.b"] = {"t.lib.core"}
        self.assertEqual(MD.cycle_problems(graph, rows),
                         ["t.e.b imports t.lib.core, which imports t.e.b back (through t.e.a): "
                          "a cycle"])
        # Through another source's shim: a real cycle too.
        graph = {"t.lib.core": {"t.lib.country"}, "t.e.a": {"t.lib.core"}, "t.e.b": set(),
                 "t.lib.country": {"t.r.country", "t.e.a"}, "t.r.country": set()}
        self.assertEqual(MD.cycle_problems(graph, rows),
                         ["t.e.a imports t.lib.core, which imports t.e.a back (through "
                          "t.lib.country): a cycle"])


# --check

def _check_repo(r: Path, edit=None, base_edit=None, files=None, table=TABLE):
    """BASE (the synthetic repository, the tools, a stand-in
    check_architecture and its baseline, FILES; then BASE_EDIT) and HEAD
    (BASE's tool run with TABLE, then EDIT), as trees."""
    _repo(r, {MD.ARCH: STAND_IN, "scripts/architecture-baseline.json": BASELINE,
              "docs/notes.md": "notes\n", "tests/notes.md": "notes\n", **(files or {})},
          tools=True)
    if base_edit:
        base_edit(r)
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    base = _git(r, "write-tree").strip()
    _write(r, MD.TABLE, table)
    run = subprocess.run([sys.executable, "-I", "-S", "-B", str(r / "scripts/move_defs.py"),
                          str(r / MD.TABLE), f"--repo={r}"], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    if edit:
        edit(r)
    _git(r, "add", "-A")
    return base, _git(r, "write-tree").strip()


def _check(r, base, head, allow=()):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        rc = MD.check(r, base, head, list(allow), MD.TABLE)
    return rc, out.getvalue()


def _edit(rel, old, new):
    def edit(r):
        t = (r / rel).read_text()
        assert old in t, old
        (r / rel).write_text(t.replace(old, new, 1))
    return edit


class TestTheCheck(unittest.TestCase):
    def test_the_tools_run_passes(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r)
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("check: 4 row(s): src/pkg/eng/__init__.py", out)
            self.assertIn("check: text: the tool wrote 6 file(s); beyond it, by hand: none", out)
            self.assertIn("HEAD is BASE's tool run on BASE, and the moves keep every name", out)
            # The documented route: BASE's copy, from git, isolated.
            tools = Path(td) / "tools"
            tools.mkdir()
            tar = subprocess.run(["git", "--no-replace-objects", "archive", base, *MD.TOOLS],
                                 cwd=r, capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "-C", str(tools)], input=tar, check=True)
            run = subprocess.run([sys.executable, "-I", "-S", str(tools / "scripts/move_defs.py"),
                                  "--check", base, head, "--trees", f"--repo={r}"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("HEAD is BASE's tool run on BASE", run.stdout)

    def test_what_fails_the_text_check(self):
        marker = "ran.txt"
        evil = f'open({marker!r}, "w").write("ran")\n'
        cases = [
            ("a statement edited", _edit("src/pkg/eng/consts.py", "_ONE = 1", "_ONE = 2"),
             "src/pkg/eng/consts.py is not what BASE's tool writes"),
            ("a comment dropped", _edit("src/pkg/eng/ca/cand.py", "# A candidate pair.\n", ""),
             "src/pkg/eng/ca/cand.py is not what"),
            ("a binding retargeted", _edit("src/pkg/eng/consts.py", "import helper as _H",
                                           "import model as _H"),
             "src/pkg/eng/consts.py is not what"),
            ("the source", _edit("src/pkg/core.py", "def kept", "def kept2"),
             "src/pkg/core.py is not what"),
            ("the tool", _edit("scripts/move_defs.py", "#!/usr/bin/env python3\n", "#!/bin/x\n"),
             "scripts/move_defs.py is not what"),
            ("a doc not allowed", _edit("docs/notes.md", "notes", "more"), "docs/notes.md is not what"),
            ("a test file", lambda r: _write(r, "tests/test_x.py", evil), "tests/test_x.py is not what"),
            ("a mode", lambda r: os.chmod(r / "src/pkg/eng/consts.py", 0o755),
             "src/pkg/eng/consts.py is not what"),
            ("a symlink", lambda r: (r / "src/pkg/link.py").symlink_to("core.py"),
             "src/pkg/link.py: a symlink or a submodule"),
            ("a .pyc", lambda r: _write(r, "src/pkg/__pycache__/core.cpython-312.pyc", "x"),
             "src/pkg/__pycache__/core.cpython-312.pyc is not what"),
            ("a baseline count", _edit("scripts/architecture-baseline.json",
                                       '"src/pkg/eng/consts.py": 1', '"src/pkg/eng/consts.py": 2'),
             "scripts/architecture-baseline.json is not what"),
            ("a table BASE's tool refuses", _edit(MD.TABLE, "    _ordinal\n", "    _ordinal _NOPE\n"),
             "BASE's tool refused HEAD's table: move_defs: src/pkg/core.py: _NOPE is no top-level"),
        ]
        for what, edit, why in cases:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, edit)
                rc, out = _check(r, base, head)
                self.assertEqual(rc, 1, out)
                self.assertIn(why, out)
                self.assertIn("nothing of HEAD's was run", out)
        # A batch that fails it has none of its code run.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, lambda r: _write(r, "src/pkg/eng/consts.py",
                                                         CONSTS + evil))
            self.assertEqual(_check(r, base, head)[0], 1)
            self.assertFalse(any(Path(td).rglob(marker)))

    def test_an_allowed_doc_is_listed_and_only_a_doc(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, lambda r: (_edit("docs/notes.md", "notes", "more")(r),
                                                   _edit("tests/notes.md", "notes", "more")(r)))
            rc, out = _check(r, base, head, ["docs/notes.md"])
            self.assertEqual(rc, 1)
            self.assertIn("by hand: ['docs/notes.md']", out)
            self.assertIn("tests/notes.md is not what", out)
            rc, out = _check(r, base, head, ["docs/notes.md", "tests/notes.md"])
            self.assertIn("tests/notes.md is not what", out)
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, _edit("docs/notes.md", "notes", "more"))
            rc, out = _check(r, base, head, ["docs/notes.md"])
            self.assertEqual(rc, 0, out)
        # Only a doc: a hand-edited module --allow names still fails.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, _edit("src/pkg/eng/consts.py", "_ONE = 1", "_ONE = 2"))
            rc, out = _check(r, base, head, ["src/pkg/eng/consts.py"])
            self.assertEqual(rc, 1, out)
            self.assertIn("check: text: src/pkg/eng/consts.py is not what BASE's tool writes", out)

    def test_a_tool_bug_the_later_checks_catch(self):
        # BASE's tool, broken, writes HEAD: the text check passes, and the
        # later checks fail what it wrote.
        tool = "scripts/move_defs.py"
        cases = [
            ("AST: a comment dropped", _edit(tool, "            first -= 1\n        return first, n.end",
                                             "            first -= 0\n            break\n"
                                             "        return first, n.end"),
             "AST: src/pkg/eng/ca/cand.py:"),
            ("bindings: an import retargeted",
             _edit(tool, 'out.append(ES.from_line("", st.module or "", names, nl) + nl)',
                   'out.append(ES.from_line("", st.module or "", [ast.alias(name=a.name.replace('
                   '"helper", "model"), asname=a.asname) for a in names], nl) + nl)'),
             "bindings: src/pkg/eng/consts.py:"),
            ("pins: a TARGET_FUNCS row pointed elsewhere",
             _edit(tool, '        target = placement.get(path.value, {}).get(name.split(".")[0])\n',
                   '        target = placement.get(path.value, {}).get(name.split(".")[0]) and '
                   '"src/pkg/eng/consts.py"\n'),
             "pins: scripts/mutation_audit.py:2: (src/pkg/eng/consts.py, _Cand.first) is not the "
             "function (src/pkg/core.py, _Cand.first) gave in BASE"),
            ("baseline: a total grown", _edit(tool, 're-exported by name (PLAN step 10)."',
                                              'return re-exported by name (PLAN step 10)."'),
             "baseline: ruff E741: 6 in HEAD, 5 in BASE"),
            ("pins: a test file written",
             _edit(tool, "        for rel, text in plan.texts.items():\n",
                   "        write('tests/x.py', '')\n        for rel, text in plan.texts.items():\n"),
             "pins: tests/x.py changed"),
        ]
        for what, base_edit, why in cases:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, base_edit=base_edit,
                                         files={"scripts/mutation_audit.py": AUDIT})
                rc, out = _check(r, base, head)
                self.assertEqual(rc, 1, out)
                self.assertIn("check: text: the tool wrote", out)
                self.assertNotIn("nothing of HEAD's was run", out)
                self.assertIn(why, out)
                self.assertIn(f"check: {what.split(':')[0]}: FAILED, ", out)


def _trees(td: Path, edit=None, files=None):
    """BASE and HEAD as folders (the tool run on a copy), HEAD then EDITed."""
    base, head = td / "base", td / "head"
    _repo(base, files)
    _repo(head, files)
    rc, out = _run(head)
    assert rc == 0, out
    if edit:
        edit(head)
    return base, head, MD.read_table(TABLE)


class TestTheLaterChecks(unittest.TestCase):
    """Each check on two folders by hand: what each one alone catches."""

    def _problems(self, kind, edit=None, files=None):
        with tempfile.TemporaryDirectory() as td:
            base, head, rows = _trees(Path(td), edit, files)
            if kind == "AST":
                return MD.ast_problems(base, head, rows)
            if kind == "bindings":
                return MD.binding_problems(base, head, rows)
            home = Path(td) / "home"
            home.mkdir()
            return MD.runtime_problems(base, head, rows, home, Path(td))

    def test_each_passes_the_tools_output(self):
        for kind in ("AST", "bindings", "runtime"):
            with self.subTest(kind):
                self.assertEqual(self._problems(kind), [])

    def test_the_ast_check(self):
        cases = [
            ("a statement edited", _edit("src/pkg/eng/consts.py", "_ONE = 1", "_ONE = 2"),
             "AST: src/pkg/eng/consts.py's statements are not BASE's"),
            ("a comment dropped", _edit("src/pkg/eng/ca/cand.py", "# A candidate pair.\n", ""),
             "AST: src/pkg/eng/ca/cand.py:"),
            ("a comment changed", _edit("src/pkg/eng/consts.py", "read once", "read twice"),
             "the comment lines above it are not BASE's (src/pkg/core.py:10)"),
            ("a statement added", lambda h: _write(h, "src/pkg/eng/consts.py", CONSTS + "X = 1\n"),
             "src/pkg/eng/consts.py's statements are not BASE's"),
            ("a store left behind", _edit("src/pkg/eng/ca/cand.py", '\n\n_Cand.__doc__ = """One candidate."""\n', "\n"),
             "src/pkg/eng/ca/cand.py's statements are not BASE's"),
            ("the source's code", _edit("src/pkg/core.py", "+ _ONE)", "+ 1)"),
             "src/pkg/core.py keeps other statements"),
            ("a package with code", _edit("src/pkg/eng/__init__.py", '"""', 'X = 1\n"""'),
             "src/pkg/eng/__init__.py holds more than a docstring"),
        ]
        for what, edit, why in cases:
            with self.subTest(what):
                got = self._problems("AST", edit)
                self.assertTrue(any(why in p for p in got), got)

    def test_the_bindings_check(self):
        cases = [
            ("an import retargeted", _edit("src/pkg/eng/consts.py", "import helper as _H",
                                           "import model as _H"),
             "bindings: src/pkg/eng/consts.py:13 reads _H, bound to [('from', 'pkg', 'model')]; "
             "in BASE [('from', 'pkg', 'helper')]"),
            ("an engine import through the source", _edit(
                "src/pkg/eng/ca/cand.py", "from pkg.eng.consts import _ZERO", "from pkg.core import _ZERO"),
             "reads _ZERO, bound to [('from', 'pkg.core', '_ZERO')]; in BASE "
             "[('from', 'pkg.eng.consts', '_ZERO')]"),
            ("a string annotation's import dropped", _edit(
                "src/pkg/eng/ca/cand.py", "from typing import List, Optional", "from typing import List"),
             "reads Optional, bound to nothing; in BASE [('from', 'typing', 'Optional')]"),
            ("a name the source reads", _edit("src/pkg/core.py", "_ordinal, _Cand", "_Cand"),
             "bindings: src/pkg/core.py:9 reads _ordinal, bound to nothing"),
            ("a builtin shadowed", _edit("src/pkg/eng/ca/cand.py", "import functools\n",
                                         "import functools\nfrom pkg.helper import fmt as int\n"),
             "reads int, bound to [('from', 'pkg.helper', 'fmt')]; in BASE nothing"),
        ]
        for what, edit, why in cases:
            with self.subTest(what):
                got = self._problems("bindings", edit)
                self.assertTrue(any(why in p for p in got), got)

    def test_the_runtime_check(self):
        model2 = MODEL + "\n\nclass Row2(Row):\n    pass\n"
        cases = [
            ("a default changed", _edit("src/pkg/eng/ca/cand.py", "size: int = 1", "size: int = 2"),
             "runtime: _Cand's shape differs: fields[1][2]"),
            ("a cache's size", _edit("src/pkg/eng/ca/cand.py", "maxsize=64", "maxsize=32"),
             "_ordinal's shape differs: params.maxsize"),
            ("a doc", _edit("src/pkg/eng/consts.py", "A figure as shown.", "A figure."),
             "_shown's shape differs: doc"),
            ("a store dropped", _edit("src/pkg/eng/ca/cand.py", '_Cand.__doc__ = """One candidate."""',
                                      ""), "_Cand's shape differs: doc"),
            ("a base class", _edit("src/pkg/eng/ca/cand.py", "class _Cand:", "class _Cand(Exception):"),
             "_Cand's shape differs: "),
            ("a module loaded extra", _edit("src/pkg/eng/consts.py", "from pkg import helper as _H",
                                            "from pkg import helper as _H\nimport pkg.extra"),
             "runtime: HEAD loads ['pkg.extra'] beyond BASE's"),
            ("a name gone", _edit("src/pkg/core.py", "_ordinal, _Cand", "_Cand"),
             "runtime: HEAD's pkg.core lacks _ordinal"),
            ("another object", _edit("src/pkg/core.py", "from pkg.eng.ca.cand import _ordinal, _Cand",
                                     "from pkg.eng.ca.cand import _Cand\n_ordinal = int"),
             "runtime: HEAD's pkg.core._ordinal is not pkg.eng.ca.cand's object"),
            ("a re-export retargeted", _edit("src/pkg/core.py", "from pkg.model import Item as Item",
                                             "from pkg.helper import fmt as Item"),
             "runtime: HEAD's pkg.core.Item is not pkg.model's object"),
            ("globals another module's", lambda h: (
                _write(h, "src/pkg/other.py", "from pkg import helper as _H\n_ZERO = 0\n\n\n"
                       "def _shown(x):\n    \"\"\"A figure as shown.\"\"\"\n"
                       "    return _H.fmt(x) if x else _ZERO\n"),
                _write(h, "src/pkg/eng/consts.py", CONSTS.split("\n\ndef _shown")[0]
                       + "\nfrom pkg.other import _shown  # noqa: E402\n")),
             "runtime: HEAD's pkg.core._shown is not pkg.eng.consts's object (its globals are "
             "another module's)"),
            ("a module said", lambda h: _write(h, "src/pkg/eng/consts.py",
                                               CONSTS + '_shown.__module__ = "pkg.core"\n'),
             "runtime: HEAD's _shown says its module is pkg.core, not pkg.eng.consts"),
            ("a new module that fails alone", _edit("src/pkg/eng/ca/cand.py", "import functools\n",
                                                    "import functools\nimport pkg.core\n"
                                                    "from pkg.core import _Cand as _C2\n"),
             "runtime: pkg.eng.ca.cand does not import alone"),
        ]
        for what, edit, why in cases:
            with self.subTest(what):
                got = self._problems("runtime", edit, {"src/pkg/model.py": model2,
                                                       "src/pkg/extra.py": ""})
                self.assertTrue(any(why in p for p in got), got)

    def test_a_wrapped_function(self):
        # A decorator with functools.wraps: the wrapper's globals are its
        # module's, in both trees; the wrapped function's are its home's,
        # and its defaults are its shape too.
        helper = HELPER + ("\n\nimport functools\n\n\ndef wrapped(fn):\n"
                           "    @functools.wraps(fn)\n    def run(*a, **k):\n"
                           "        return fn(*a, **k)\n    return run\n")
        core = CORE.replace("def _shown(x):", "@_H.wrapped\ndef _shown(x, places=2):").replace(
            "\n\ndef kept", "\n\n@_H.wrapped\ndef kept2(x=3):\n    return x\n\n\ndef kept")
        files = {"src/pkg/helper.py": helper, "src/pkg/core.py": core}
        self.assertEqual(self._problems("runtime", files=files), [])
        got = self._problems("runtime", _edit("src/pkg/eng/consts.py", "places=2", "places=3"),
                             files)
        self.assertTrue(any("_shown's shape differs: wrapped.defaults" in p for p in got), got)

    def test_the_pins_and_layers(self):
        self.assertEqual(MD.pin_problems(["src/a.py", "docs/x.md"]), [])
        with tempfile.TemporaryDirectory() as td:
            files = {"scripts/mutation_audit.py": AUDIT}
            base, head, rows = _trees(Path(td), files=files)
            self.assertEqual(MD.mutation_problems(base, head), [])
            _edit("src/pkg/eng/ca/cand.py", "return rows or", "return None or")(head)
            self.assertEqual(MD.mutation_problems(base, head), [
                "pins: scripts/mutation_audit.py:2: (src/pkg/eng/ca/cand.py, _Cand.first) is not "
                "the function (src/pkg/core.py, _Cand.first) gave in BASE"])
            _edit("scripts/mutation_audit.py", '"kept"', '"_shown"')(head)
            self.assertEqual(MD.mutation_problems(base, head), [
                "pins: scripts/mutation_audit.py's TARGET_FUNCS names other functions in HEAD"])
        self.assertEqual(len(MD.pin_problems(["tests/golden/x.json", "packages/p/tests/t.py",
                                              "tests/test_x.py"])), 3)
        with tempfile.TemporaryDirectory() as td:
            base, head, rows = _trees(Path(td))
            self.assertEqual(MD._layers_check(head, rows, rows), [])
            _write(head, "src/pkg/eng/us/__init__.py", "")
            _write(head, "src/pkg/eng/us/x.py", "def f():\n    from pkg.eng.ca import cand\n")
            _edit("src/pkg/eng/consts.py", "from pkg import helper as _H",
                  "from pkg import helper as _H\nimport pkg.core")(head)
            got = MD._layers_check(head, rows, rows)
            self.assertEqual(got, [
                "layers: src/pkg/eng/us/x.py: pkg.eng.us.x imports pkg.eng.ca: one country's "
                "engine never reads the other's",
                "layers: src/pkg/eng/us/x.py: pkg.eng.us.x imports pkg.eng.ca.cand: one "
                "country's engine never reads the other's",
                "layers: pkg.eng.consts imports pkg.core, which imports pkg.eng.consts back: "
                "a cycle"])

    def test_head_s_check_architecture_must_pass(self):
        with tempfile.TemporaryDirectory() as td:
            base, head, rows = _trees(Path(td))
            _write(head, MD.ARCH, "import sys\nprint('it got worse')\nsys.exit(1)\n")
            got = MD._layers_check(head, [r for r in rows], rows)
            self.assertEqual(len(got), 1)
            self.assertIn("layers: HEAD's check_architecture fails: it got worse", got[0])


class TestTheCheckRuns(unittest.TestCase):
    def test_bases_copy_is_what_runs(self):
        def marked(r):
            f = r / "scripts/move_defs.py"
            f.write_text(f.read_text().replace("def main(argv=None) -> int:\n",
                                               "def main(argv=None) -> int:\n"
                                               "    print('BASE COPY RAN')\n", 1))
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, base_edit=marked)
            run = subprocess.run([sys.executable, "-I", "-S",
                                  str(_paths.REPO / "scripts/move_defs.py"), "--check",
                                  base, head, "--trees", f"--repo={r}"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("BASE COPY RAN", run.stdout)

    def test_the_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            _git(r, "init", "-q")
            _git(r, "add", "-A")
            tree = _git(r, "write-tree").strip()
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(MD.main(["--check", tree, tree, f"--repo={r}"]), 2)
                self.assertEqual(MD.main(["--check", tree, tree, "--trees", f"--repo={r}"]), 2)
                self.assertEqual(MD.main(["--check", "--dry-run", f"--repo={r}"]), 2)
                self.assertEqual(MD.main(["a", "b"]), 2)
                self.assertEqual(MD.main([]), 2)
            self.assertIn("are commits", err.getvalue())
            self.assertIn("BASE lacks one of scripts/move_defs.py", err.getvalue())
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(MD.check(r, base, base, [], MD.TABLE), 2)
                self.assertEqual(MD.check(r, head, head, [], MD.TABLE), 2)
            self.assertIn(f"HEAD has no {MD.TABLE}: no batch", err.getvalue())
            self.assertIn("has no row whose target BASE lacks: no batch", err.getvalue())


SPY = """import glob
import importlib
import inspect
import os
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pkg.core
import pkg.core as core
from pkg import core as c2
from pkg import core as c3

REPO = Path(".")
FOLDER = Path(core.__file__).resolve().parent
MOD = "pkg.core"
twice = later
LIB: Path = Path(core.__file__).parent
MOD2: str = "pkg.core"
ROOT = Path(core.__file__).parents[1]
UP = Path(core.__file__).parents[1]
UP = Path(core.__file__).parent
PREFIX = "pkg.core."
LIB2, X2 = Path(core.__file__).parent, 1
X3, (X4, LIB4) = 1, (2, Path(core.__file__).parent)
C3 = B3
READ3 = C3 / "core.py"
g9 = lambda lib9=FOLDER: lib9 / "core.py"


def chain():
    B3 = A3

    def inner():
        A3 = Path(core.__file__).parent
        return A3
    return B3, inner


def bind():
    global later
    later = importlib.import_module("pkg.core")


def seams(monkeypatch):
    core._ONE = 2
    pkg.core._ONE = 2
    c2._shown = None
    del c3._ZERO
    mock.patch("pkg.core._Cand")
    mock.patch.object(core, "_ordinal")
    mock.patch.multiple(core, _shown=None)
    mock.patch.multiple("pkg.core", _ONE=3)
    setattr(core, "_ONE", 4)
    monkeypatch.setattr(c2, "_ZERO", 1)
    vars(core)["_ONE"] = 5
    core.__dict__.update(_ZERO=6)
    mock.patch.dict(vars(core), {"_shown": None})
    ns = vars(core)
    ns["_ONE"] = 7
    importlib.import_module("pkg.core")._ONE = 8
    m = importlib.import_module("pkg.core")
    m._ZERO = 9
    sys.modules["pkg.core"]._ONE = 10
    Path("hook.py").write_text("from pkg import core\\ncore._ordinal = None\\n")
    core._H = None
    setattr(core, monkeypatch.name, 1)
    twice._ZERO = 12
    mock.patch(f"pkg.core.{monkeypatch.name}")
    mock.patch.object(core, attribute="_shown")
    mock.patch.object(target=c2, attribute="_ONE")
    mock.patch("pkg.core" + "._shown")
    mock.patch.object(sys.modules.get("pkg.core"), "kept")
    Path("hook2.py").write_text(textwrap.dedent('''
        from pkg import core
        core._ONE = None
    '''))
    Path("hook3.py").write_text(f"from pkg import core\\ncore._ordinal = {1}\\n")
    Path("hook4.py").write_text("from pkg import core\\ncore._ZERO = %d\\n" % 1)
    Path("hook5.py").write_text("from pkg import core\\ncore._shown = (\\n")
    Path("hook6.py").write_text("from pkg import core\\ncore._ONE = {0!r}\\n".format(1))
    Path("hook7.py").write_text("from pkg import core\\n" + "core._ZERO = None\\n")
    mock.patch("pkg.core", None)
    importlib.import_module(name="pkg.core")
    print(MOD3 := "pkg.core")
    importlib.reload(core)
    sys.modules.pop("pkg.core")
    mock.patch.dict(sys.modules, {"pkg.core": None})
    mock.patch("pkg.core.%s" % monkeypatch.name)
    mock.patch("pkg.core.{}".format(monkeypatch.name))
    mock.patch("pkg.core._" + monkeypatch.name)
    mock.patch("%s._shown" % "pkg.core")
    mock.patch("{}._shown".format("pkg.core"))
    Path("hook8.py").write_text(f"from pkg import core\\ncore.{monkeypatch.name} = None\\n")
    Path("hook9.py").write_text(f"from pkg import core\\nsetattr(core, '{monkeypatch.name}', 1)\\n")
    Path("hook10.py").write_text(f"from unittest import mock\\nmock.patch('pkg.core.{monkeypatch.name}')\\n")
    Path("hook11.py").write_text("x = open('src/pkg/core.py')\\ny = (\\n")
    Path("hook12.py").write_text("import pkg.core\\ny = (\\n")
    Path("hook13.py").write_text(f"from pkg import core\\nfrom unittest import mock\\n"
                                 f"mock.patch.multiple(core, {monkeypatch.name}=1)\\n")
    Path("hook14.py").write_text(f"from pkg import core\\nvars(core)['{monkeypatch.name}'] = 1\\n")
    stub = {}
    stub["pkg.core"] = None
    mock.patch("%s.%s" % ("pkg.core", "_shown"))
    Path("hook15.py").write_text(textwrap.dedent(monkeypatch.name) + "from pkg import core\\ncore._ONE = 1\\n")
    mock.patch(monkeypatch.name + "pkg.core._ONE")
    Path("hook16.py").write_text(f"{monkeypatch.name}from pkg import core\\ncore._ONE = 1\\n")
    Path("hook17.py").write_text("%sfrom pkg import core\\ncore._ONE = 1\\n" % monkeypatch.name)
    del stub["pkg.core"]
    mock.patch("pkg.core._Cand.first")
    return inspect.getsource(core)


def at(lib4=FOLDER):
    return lib4 / "core.py"


def at2(*, lib5=FOLDER):
    return lib5 / "core.py"


def at3(x, lib8=FOLDER):
    return lib8 / "core.py"


def readers():
    folder = os.path.dirname(core.__file__)
    (lib2 := Path(core.__file__).parent)
    lib6 = lib2
    for lib3 in (FOLDER,):
        pass
    for lib10, _n in [(FOLDER, 1)]:
        pass
    return (core.__file__,
            REPO / "src" / "pkg" / "core.py",
            os.path.join("pkg", "core.py"),
            "src/pkg/core.py",
            f"{REPO}/src/pkg/core.py",
            inspect.getsourcefile(core._shown),
            core._Cand.__module__,
            ("src/pkg/core.py", "_shown"),
            FOLDER / "core.py",
            os.path.join(os.path.dirname(core.__file__), "core.py"),
            Path(core.__file__).parent / "core.py",
            Path(core.__file__).read_text(),
            os.path.join(folder, "core.py"),
            lib2 / "core.py",
            lib3 / "core.py",
            LIB / "core.py",
            os.path.dirname(core.__file__) + "/core.py",
            f"{os.path.dirname(core.__file__)}/core.py",
            Path(core.__file__).parents[1] / "pkg/core.py",
            Path(core.__file__).absolute().parent / "core.py",
            Path(core.__file__).parent.joinpath("core.py"),
            Path(core.__file__).parent.glob("*.py"),
            FOLDER.rglob("*.py"),
            LIB.iterdir(),
            os.listdir(folder),
            os.scandir(FOLDER),
            os.walk(FOLDER),
            glob.glob(os.path.join(folder, "*.py")),
            glob.iglob(f"{folder}/*.py"),
            ROOT.glob("*.py"),
            (ROOT / "pkg").glob("*.py"),
            lib6 / "core.py",
            (lib7 := Path(core.__file__).parent) / "core.py",
            glob.glob(str(FOLDER) + "/*.py"),
            glob.glob(str(FOLDER / "*.py")),
            (FOLDER / ".." / "pkg").iterdir(),
            os.path.dirname(core.__file__) + os.sep + "core.py",
            ROOT.rglob("*/core.py"),
            UP.glob("eng/*.py"),
            LIB.parent.glob("*.py"),
            os.listdir(os.path.dirname(folder)),
            ROOT.rglob("core.py"),
            ROOT.glob("**/core.py"),
            Path(core.__file__).parents[2].rglob("pkg/*.py"),
            LIB2 / "core.py",
            LIB4 / "core.py",
            lib10 / "core.py",
            os.path.join(os.fspath(LIB), "core.py"),
            Path(os.path.dirname(core.__file__)) / "core.py",
            os.path.join(os.path.realpath(os.path.dirname(core.__file__)), "core.py"),
            Path(core.__file__).parent.absolute() / "core.py",
            Path(core.__file__).parent.resolve() / "core.py",
            [d8 / "core.py" for d8 in [FOLDER]],
            FOLDER.walk(),
            (Path(core.__file__).parents[2] / "src" / "pkg").iterdir(),
            Path(core.__file__).parents[2].glob("src/pkg/*.py"),
            (ROOT / "." / "pkg").glob("*.py"),
            ROOT.glob("pk?/*.py"),
            ROOT.glob("[p]kg/*.py"),
            ROOT.glob(MOD))


def fine():
    '''What pkg.core keeps:
    the kept (all of it.'''
    vars(core)["kept"] = 1
    lib = Path(core.__file__).parent
    root = Path(core.__file__).resolve().parents[1]
    here = os.path.dirname(os.path.realpath(core.__file__))
    note = "core values:\\n  one, two (\\n"
    see = "see pkg.core for (more"
    folders = (os.path.dirname(os.path.abspath(core.__file__)),
               os.path.dirname(os.path.normpath(core.__file__)),
               Path(core.__file__).parent.parent,
               os.path.dirname(os.path.dirname(core.__file__)),
               Path(core.__file__).resolve().absolute().parent,
               Path(core.__file__).absolute().parent)
    listed = (root.rglob("*.py"), (root / "eng").rglob("*.py"), os.walk(root),
              lib.parent.rglob("*.py"), root.glob("eng/*.py"), root.glob("**/*.py"),
              (root / "eng").glob("*.py"), root.rglob("*"))
    return (lib, root, here, FOLDER / "eng", note, see, folders, listed, "pkg.core" in sys.modules,
            getattr(core, "_shown"), mock.patch("pkg.core.kept"), core.kept,
            inspect.getsource(core._shown), sorted(vars(core).items()), dict(vars(core)),
            {"src/pkg/core.py": ""}, ("src/pkg/core.py", "kept"), "_ONE" in vars(core),
            mock.patch("pkg.eng.consts._ONE"), "see src/pkg/core.py for more")
"""
# Each line of SPY that is a seam, and what the stage says of it.
SPY_SEAMS = [
    ("core._ONE = 2", "stores into pkg.core._ONE (src/pkg/eng/consts.py binds it): pkg.core no "
                      "longer defines it"),
    ("pkg.core._ONE = 2", "stores into pkg.core._ONE"),
    ("c2._shown = None", "stores into pkg.core._shown"),
    ("del c3._ZERO", "stores into pkg.core._ZERO"),
    ('mock.patch("pkg.core._Cand")', "names pkg.core._Cand (src/pkg/eng/ca/cand.py binds _Cand)"),
    ('mock.patch.object(core, "_ordinal")', "patches pkg.core._ordinal"),
    ("mock.patch.multiple(core, _shown=None)", "patches pkg.core._shown"),
    ('mock.patch.multiple("pkg.core", _ONE=3)', "patches pkg.core._ONE"),
    ('setattr(core, "_ONE", 4)', "stores into pkg.core._ONE"),
    ('monkeypatch.setattr(c2, "_ZERO", 1)', "stores into pkg.core._ZERO"),
    ('vars(core)["_ONE"] = 5', "stores into pkg.core's namespace (_ONE)"),
    ("core.__dict__.update(_ZERO=6)", "stores into pkg.core's namespace (_ZERO)"),
    ('mock.patch.dict(vars(core), {"_shown": None})', "stores into pkg.core's namespace (_shown)"),
    ('ns["_ONE"] = 7', "stores into pkg.core's namespace (_ONE)"),
    ('importlib.import_module("pkg.core")._ONE = 8', "stores into pkg.core._ONE"),
    ("m._ZERO = 9", "stores into pkg.core._ZERO"),
    ('sys.modules["pkg.core"]._ONE = 10', "stores into pkg.core._ONE"),
    ('Path("hook.py").write_text("from pkg import core\\ncore._ordinal = None\\n")',
     "stores into pkg.core._ordinal"),
    ("core._H = None", "stores into pkg.core._H (src/pkg/eng/consts.py binds it)"),
    ("setattr(core, monkeypatch.name, 1)", "stores into pkg.core.<computed>"),
    ("return inspect.getsource(core)", "getsource(pkg.core): a whole-module read B0b's design "
                                       "does not account for"),
    ("return (core.__file__,", "reads pkg.core.__file__: a reader of pkg.core's file or text"),
    ('REPO / "src" / "pkg" / "core.py",', "names its path 'src/pkg/core.py'"),
    ('os.path.join("pkg", "core.py"),', "names its path 'pkg/core.py'"),
    ('"src/pkg/core.py",', "names its path 'src/pkg/core.py'"),
    ('f"{REPO}/src/pkg/core.py",', "names its path '/src/pkg/core.py'"),
    ("inspect.getsourcefile(core._shown),", "getsourcefile(pkg.core._shown)"),
    ("core._Cand.__module__,", "reads pkg.core._Cand.__module__"),
    ('("src/pkg/core.py", "_shown"),', "names its path 'src/pkg/core.py'"),
    ('MOD = "pkg.core"', "names pkg.core itself (bound to a name): the stage cannot read what "
                         "it reaches"),
    ('later = importlib.import_module("pkg.core")', "names pkg.core itself (passed to "
                                                    "import_module)"),
    ('m = importlib.import_module("pkg.core")', "names pkg.core itself (passed to import_module)"),
    ("twice._ZERO = 12", "stores into pkg.core._ZERO"),
    ('mock.patch(f"pkg.core.{monkeypatch.name}")', "names pkg.core.<computed>"),
    ('mock.patch.object(core, attribute="_shown")', "patches pkg.core._shown"),
    ('mock.patch.object(target=c2, attribute="_ONE")', "patches pkg.core._ONE"),
    ('mock.patch("pkg.core" + "._shown")', "names pkg.core itself (a target built at run time)"),
    ('mock.patch.object(sys.modules.get("pkg.core"), "kept")',
     "names pkg.core itself (passed to sys.modules.get)"),
    ("Path(\"hook2.py\").write_text(textwrap.dedent('''", "stores into pkg.core._ONE"),
    ('Path("hook3.py").write_text(f"from pkg import core\\ncore._ordinal = {1}\\n")',
     "stores into pkg.core._ordinal"),
    ('Path("hook4.py").write_text("from pkg import core\\ncore._ZERO = %d\\n" % 1)',
     "stores into pkg.core._ZERO"),
    ('Path("hook5.py").write_text("from pkg import core\\ncore._shown = (\\n")',
     "a script naming pkg.core that does not parse: its seams cannot be read"),
    ('Path("hook6.py").write_text("from pkg import core\\ncore._ONE = {0!r}\\n".format(1))',
     "stores into pkg.core._ONE"),
    ('Path("hook7.py").write_text("from pkg import core\\n" + "core._ZERO = None\\n")',
     "stores into pkg.core._ZERO"),
    ('mock.patch("pkg.core", None)', "names pkg.core itself (a patch target)"),
    ('importlib.import_module(name="pkg.core")', "names pkg.core itself (passed to import_module)"),
    ('MOD2: str = "pkg.core"', "names pkg.core itself (bound to a name)"),
    ('print(MOD3 := "pkg.core")', "names pkg.core itself (bound to a name)"),
    ("importlib.reload(core)", "reloads pkg.core: the shim runs again, not the modules its names "
                               "moved to: the stage cannot read what it reaches"),
    ('sys.modules.pop("pkg.core")', "names pkg.core itself (passed to sys.modules.pop)"),
    ('mock.patch.dict(sys.modules, {"pkg.core": None})', "names pkg.core itself (a dict key)"),
    ('mock.patch("pkg.core.%s" % monkeypatch.name)', "names pkg.core.<computed>: the stage cannot"),
    ('mock.patch("pkg.core.{}".format(monkeypatch.name))', "names pkg.core.<computed>"),
    ('mock.patch("pkg.core._" + monkeypatch.name)', "names pkg.core._<computed>"),
    ('mock.patch("%s._shown" % "pkg.core")', "names pkg.core itself (a target built at run time)"),
    ('mock.patch("{}._shown".format("pkg.core"))',
     "names pkg.core itself (a target built at run time)"),
    ('Path("hook8.py").write_text(f"from pkg import core\\ncore.{monkeypatch.name} = None\\n")',
     "stores into pkg.core.<computed>"),
    ('Path("hook9.py").write_text(f"from pkg import core\\nsetattr(core, \'{monkeypatch.name}\', '
     '1)\\n")', "stores into pkg.core.<computed>"),
    ('Path("hook10.py").write_text(f"from unittest import mock\\nmock.patch(\'pkg.core.'
     '{monkeypatch.name}\')\\n")', "names pkg.core.<computed>"),
    ('Path("hook11.py").write_text("x = open(\'src/pkg/core.py\')\\ny = (\\n")',
     "a script naming pkg.core that does not parse"),
    ('Path("hook12.py").write_text("import pkg.core\\ny = (\\n")',
     "a script naming pkg.core that does not parse"),
    ('FOLDER / "core.py",', "reads its file through its folder ('core.py')"),
    ('os.path.join(os.path.dirname(core.__file__), "core.py"),',
     "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parent / "core.py",', "reads its file through its folder ('core.py')"),
    ("Path(core.__file__).read_text(),", "reads pkg.core.__file__"),
    ('os.path.join(folder, "core.py"),', "reads its file through its folder ('core.py')"),
    ('return lib4 / "core.py"', "reads its file through its folder ('core.py')"),
    ('lib2 / "core.py",', "reads its file through its folder ('core.py')"),
    ('lib3 / "core.py",', "reads its file through its folder ('core.py')"),
    ('LIB / "core.py",', "reads its file through its folder ('core.py')"),
    ('os.path.dirname(core.__file__) + "/core.py",', "reads its file through its folder ('core.py')"),
    ('f"{os.path.dirname(core.__file__)}/core.py",', "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parents[1] / "pkg/core.py",',
     "reads its file through its folder ('pkg/core.py')"),
    ('Path(core.__file__).absolute().parent / "core.py",',
     "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parent.joinpath("core.py"),', "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parent.glob("*.py"),', "lists its folder (glob): the moved code is not "
                                                 "there: a reader of pkg.core's file or text"),
    ('FOLDER.rglob("*.py"),', "lists its folder (rglob)"),
    ("LIB.iterdir(),", "lists its folder (iterdir)"),
    ("os.listdir(folder),", "lists its folder (listdir)"),
    ("os.scandir(FOLDER),", "lists its folder (scandir)"),
    ("os.walk(FOLDER),", "lists its folder (walk)"),
    ('glob.glob(os.path.join(folder, "*.py")),', "lists its folder (glob)"),
    ('glob.iglob(f"{folder}/*.py"),', "lists its folder (iglob)"),
    ('ROOT.glob("*.py"),', "lists its folder or a parent (glob)"),
    ('(ROOT / "pkg").glob("*.py"),', "lists its folder (glob)"),
    ("ROOT.glob(MOD))", "lists its folder or a parent (glob, by a pattern it cannot read)"),
    ('PREFIX = "pkg.core."', "names pkg.core.<computed>"),
    ('Path("hook13.py").write_text(f"from pkg import core\\nfrom unittest import mock\\n"',
     "patches pkg.core.<computed>"),
    ('Path("hook14.py").write_text(f"from pkg import core\\nvars(core)[\'{monkeypatch.name}\'] = 1\\n")',
     "stores into pkg.core's namespace (names it cannot read)"),
    ('stub["pkg.core"] = None', "names pkg.core itself (a key stored into a mapping)"),
    ('mock.patch("%s.%s" % ("pkg.core", "_shown"))',
     "names pkg.core itself (a target built at run time)"),
    ('return lib5 / "core.py"', "reads its file through its folder ('core.py')"),
    ('lib6 / "core.py",', "reads its file through its folder ('core.py')"),
    ('(lib7 := Path(core.__file__).parent) / "core.py",', "reads its file through its folder"),
    ('glob.glob(str(FOLDER) + "/*.py"),', "lists its folder (glob)"),
    ('glob.glob(str(FOLDER / "*.py")),', "lists its folder (glob)"),
    ('(FOLDER / ".." / "pkg").iterdir(),', "lists its folder (iterdir)"),
    ('os.path.dirname(core.__file__) + os.sep + "core.py",',
     "reads its file through its folder ('core.py')"),
    ('ROOT.rglob("*/core.py"),', "lists its folder or a parent (rglob)"),
    ('UP.glob("eng/*.py"),', "lists its folder (glob)"),
    ('LIB.parent.glob("*.py"),', "lists its folder or a parent (glob)"),
    ("os.listdir(os.path.dirname(folder)),", "lists its folder or a parent (listdir)"),
    ('Path("hook15.py").write_text(textwrap.dedent(monkeypatch.name) + "from pkg import core\\ncore._ONE '
     '= 1\\n")', "stores into pkg.core._ONE (src/pkg/eng/consts.py binds it)"),
    ('mock.patch(monkeypatch.name + "pkg.core._ONE")',
     "names pkg.core._ONE (src/pkg/eng/consts.py binds _ONE)"),
    ('Path("hook16.py").write_text(f"{monkeypatch.name}from pkg import core\\ncore._ONE = 1\\n")',
     "a script naming pkg.core that does not parse"),
    ('Path("hook17.py").write_text("%sfrom pkg import core\\ncore._ONE = 1\\n" % monkeypatch.name)',
     "a script naming pkg.core that does not parse"),
    ('del stub["pkg.core"]', "names pkg.core itself (a key stored into a mapping)"),
    ('mock.patch("pkg.core._Cand.first")',
     "names pkg.core._Cand.first (src/pkg/eng/ca/cand.py binds _Cand)"),
    ('READ3 = C3 / "core.py"', "reads its file through its folder ('core.py')"),
    ('g9 = lambda lib9=FOLDER: lib9 / "core.py"', "reads its file through its folder ('core.py')"),
    ('return lib8 / "core.py"', "reads its file through its folder ('core.py')"),
    ('ROOT.rglob("core.py"),', "lists its folder or a parent (rglob)"),
    ('ROOT.glob("**/core.py"),', "lists its folder or a parent (glob)"),
    ('Path(core.__file__).parents[2].rglob("pkg/*.py"),', "lists its folder or a parent (rglob)"),
    ('LIB2 / "core.py",', "reads its file through its folder ('core.py')"),
    ('LIB4 / "core.py",', "reads its file through its folder ('core.py')"),
    ('lib10 / "core.py",', "reads its file through its folder ('core.py')"),
    ('os.path.join(os.fspath(LIB), "core.py"),', "reads its file through its folder ('core.py')"),
    ('Path(os.path.dirname(core.__file__)) / "core.py",',
     "reads its file through its folder ('core.py')"),
    ('os.path.join(os.path.realpath(os.path.dirname(core.__file__)), "core.py"),',
     "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parent.absolute() / "core.py",',
     "reads its file through its folder ('core.py')"),
    ('Path(core.__file__).parent.resolve() / "core.py",',
     "reads its file through its folder ('core.py')"),
    ('[d8 / "core.py" for d8 in [FOLDER]],', "reads its file through its folder ('core.py')"),
    ("FOLDER.walk(),", "lists its folder (walk)"),
    ('(Path(core.__file__).parents[2] / "src" / "pkg").iterdir(),', "lists its folder (iterdir)"),
    ('Path(core.__file__).parents[2].glob("src/pkg/*.py"),', "lists its folder (glob)"),
    ('(ROOT / "." / "pkg").glob("*.py"),', "lists its folder (glob)"),
    ('ROOT.glob("pk?/*.py"),', "lists its folder or a parent (glob)"),
    ('ROOT.glob("[p]kg/*.py"),', "lists its folder or a parent (glob)"),
]


def _line(text: str, line: str) -> int:
    return next(i for i, x in enumerate(text.splitlines(), 1) if x.strip() == line)


class TestTheSeams(unittest.TestCase):
    """--check's seams stage: a patch or store through a source of a name
    it no longer defines, or a reader of its file or text, fails."""

    def test_every_form(self):
        with tempfile.TemporaryDirectory() as td:
            base, head, rows = _trees(Path(td), files={"tests/test_spy.py": SPY})
            files, got, left = MD.seam_problems(head, rows)
            self.assertEqual((files, left), (1, []))
            for line, why in SPY_SEAMS:
                at = f"seams: tests/test_spy.py:{_line(SPY, line)}: "
                self.assertTrue(any(p.startswith(at) and why in p for p in got), (line, got))
            fine = range(_line(SPY, "def fine():"), len(SPY.splitlines()) + 1)
            seen = {int(p.split(":")[2]) for p in got}
            self.assertEqual(seen & set(fine), set(), got)
            self.assertEqual(seen, {_line(SPY, x) for x, _w in SPY_SEAMS})

    def test_the_whole_module_reads_b0b_names(self):
        # A patch first: the count is the whole reads', not any seam's.
        run = "import inspect\nimport pkg.core as core\ncore._shown = None\n" \
              "A = inspect.getsource(core)\nB = inspect.getsourcelines(core)\n"
        files = {"tests/test_engine_run.py": run, "tests/rules/test_engine_run.py": run}
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                MD.B0B_WHOLE_READS, {"tests/test_engine_run.py": ("pkg.core", 1)}, clear=True):
            base, head, rows = _trees(Path(td), files=files)
            _n, got, left = MD.seam_problems(head, rows)
            self.assertEqual(left, ["tests/test_engine_run.py: 1 whole-module read(s) of pkg.core"])
            # Over the count, another kind, a file of the same name elsewhere: each fails.
            self.assertEqual([p.split(": ")[1] for p in got], [
                "tests/rules/test_engine_run.py:3", "tests/rules/test_engine_run.py:4",
                "tests/rules/test_engine_run.py:5", "tests/test_engine_run.py:3",
                "tests/test_engine_run.py:5"])
        # A whole read of a module the entry does not name fails; the entry,
        # for a module the table does not move out of, asks nothing.
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                MD.B0B_WHOLE_READS, {"tests/test_engine_run.py": ("pkg.other", 5)}, clear=True):
            base, head, rows = _trees(Path(td), files={"tests/test_engine_run.py": run})
            _n, got, left = MD.seam_problems(head, rows)
            self.assertEqual((left, len(got)), ([], 3))

    def test_the_whole_module_reads_count_exactly(self):
        # Fewer reads than the entry records fail too (B0b lowers it), the
        # file gone among them; the entry's count passes.
        run = "import inspect\nimport pkg.core as core\nA = inspect.getsource(core)\n"
        for files, want, problems, left in (
                ({"tests/test_engine_run.py": run}, 1, [],
                 ["tests/test_engine_run.py: 1 whole-module read(s) of pkg.core"]),
                ({"tests/test_engine_run.py": run}, 3, [
                    "seams: tests/test_engine_run.py: 1 whole-module read(s) of pkg.core, "
                    "B0B_WHOLE_READS records 3: lower the entry to 1 (at 0, remove it)"], []),
                ({}, 2, ["seams: tests/test_engine_run.py: 0 whole-module read(s) of pkg.core, "
                         "B0B_WHOLE_READS records 2: lower the entry to 0 (at 0, remove it)"], [])):
            with self.subTest(want=want, files=list(files)), tempfile.TemporaryDirectory() as td, \
                    mock.patch.dict(MD.B0B_WHOLE_READS,
                                    {"tests/test_engine_run.py": ("pkg.core", want)}, clear=True):
                base, head, rows = _trees(Path(td), files=files)
                _n, got, notes = MD.seam_problems(head, rows)
                self.assertEqual((got, notes), (problems, left))

    def test_b0bs_helper_passes_by_its_path_at_its_count(self):
        files = {"tests/_engine.py": ENGINE_HELPER, "tests/sub/_engine.py": ENGINE_HELPER}
        elsewhere = [f"tests/sub/_engine.py:{_line(ENGINE_HELPER, x)}" for x in (
            "stack.enter_context(mock.patch.object(core, name, new))",
            "return inspect.getsource(core) + \"\".join(p.read_text() for p in "
            "sorted(ENGINE.rglob(\"*.py\")))",
            "for p in [Path(core.__file__), *sorted(ENGINE.rglob(\"*.py\"))]:",
            "return rel == \"pkg/core.py\" or rel.startswith(\"eng/\")")]
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(MD, "B0B_HELPER", ("tests/_engine.py", 4)):
            base, head, rows = _trees(Path(td), files=files)
            _n, got, left = MD.seam_problems(head, rows)
            self.assertEqual(left, ["tests/_engine.py: 4 seam(s), B0b's helper"])
            # Elsewhere, only what a move breaks fails: not the engine's folder.
            self.assertEqual([p.split(": ")[1] for p in got], elsewhere)
        # Any other count fails: the recorded one (0 until B0b) among them.
        for count in (MD.B0B_HELPER[1], 3, 5):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td, \
                    mock.patch.object(MD, "B0B_HELPER", ("tests/_engine.py", count)):
                base, head, rows = _trees(Path(td), files=files)
                _n, got, left = MD.seam_problems(head, rows)
                self.assertEqual(left, [])
                self.assertEqual(got[len(elsewhere):], [
                    f"seams: tests/_engine.py: 4 seam(s), B0B_HELPER records {count}: B0b's "
                    f"helper passes at its reviewed count only (set it with the review of its "
                    f"seams)"])
        # A helper that does not parse fails, at any count.
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(MD, "B0B_HELPER", ("tests/_engine.py", 1)):
            base, head, rows = _trees(Path(td), files={"tests/_engine.py": "def f(:\n"})
            _n, got, left = MD.seam_problems(head, rows)
            self.assertEqual(got, [
                "seams: tests/_engine.py: does not parse: its seams cannot be read",
                "seams: tests/_engine.py: 0 seam(s), B0B_HELPER records 1: B0b's helper passes "
                "at its reviewed count only (set it with the review of its seams)"])
        # No helper, a count recorded: it fails as well.
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(MD, "B0B_HELPER", ("tests/_engine.py", 2)):
            base, head, rows = _trees(Path(td))
            _n, got, left = MD.seam_problems(head, rows)
            self.assertEqual(got, ["seams: tests/_engine.py: 0 seam(s), B0B_HELPER records 2: "
                                   "B0b's helper passes at its reviewed count only (set it with "
                                   "the review of its seams)"])

    def test_a_module_swapped_in_sys_modules_fails(self):
        # A fake set in its place, and its `del`: the engine's modules never
        # see it (they import nothing from the shim).
        code = ('import sys\nfrom unittest import mock\nfake = object()\n'
                'sys.modules["pkg.core"] = fake\ndel sys.modules["pkg.core"]\n')
        seams = MD.Seams({"pkg.core": {"_ONE": "src/pkg/eng/consts.py"}}, {"pkg.core": "core.py"})
        self.assertEqual([(g[0], g[2], g[3]) for g in seams.scan(ast.parse(code))], [
            (4, "names pkg.core itself (a sys.modules key)", "blind"),
            (5, "names pkg.core itself (a sys.modules key)", "blind")])

    def test_a_scripts_escapes_warn_nothing(self):
        inner = "from pkg import core\nx = '" + "\\" + "d'\ncore._ONE = 1\n"
        seams = MD.Seams({"pkg.core": {"_ONE": "src/pkg/eng/consts.py"}}, {"pkg.core": "core.py"})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            got = seams.scan(ast.parse("S = " + repr(inner) + "\n"))
        self.assertEqual([str(x.message) for x in w], [])
        self.assertEqual([g[2] for g in got], ["stores into pkg.core._ONE (src/pkg/eng/consts.py "
                                               "binds it)"])

    def test_a_check_fails_at_its_seams(self):
        spy = "import pkg.core as core\n\n\ndef f():\n    core._shown = None\n"
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"tests/test_spy.py": spy,
                                               "packages/p/tests/test_p.py": spy.replace(
                                                   "_shown", "kept")})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("check: seams: FAILED, 1 problem(s), listed below\n", out)
            self.assertIn("check: seams: tests/test_spy.py:5: stores into pkg.core._shown "
                          "(src/pkg/eng/consts.py binds it)", out)
            self.assertNotIn("test_p.py", out)
            self.assertEqual(out.count("check: seams: "), 2, out)
            self.assertNotIn("nothing of HEAD's was run", out)
        run = "import inspect\nimport pkg.core as core\nA = inspect.getsource(core)\n"
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
                MD.B0B_WHOLE_READS, {"tests/test_engine_run.py": ("pkg.core", 1)}, clear=True):
            r = Path(td)
            base, head = _check_repo(r, files={"tests/test_engine_run.py": run})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("check: seams: 2 file(s) of tests, scripts, packages/*/tests: none of "
                          "the forms", out)
            self.assertIn("check: seams: left to B0b: tests/test_engine_run.py: 1 whole-module "
                          "read(s) of pkg.core\n", out)


# B0b's helper as its design draws it (patch_engine, source, file_with,
# WIDENED as a predicate), for the synthetic package.
ENGINE_HELPER = '''"""The engine's seams."""
import inspect
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pkg.core as core

ENGINE = Path(core.__file__).resolve().parents[0] / "eng"


def patch_engine(name, new):
    stack = ExitStack()
    stack.enter_context(mock.patch.object(core, name, new))
    for k, m in list(sys.modules.items()):
        if k.startswith("pkg.eng"):
            stack.enter_context(mock.patch.object(m, name, new))
    return stack


def source():
    return inspect.getsource(core) + "".join(p.read_text() for p in sorted(ENGINE.rglob("*.py")))


def file_with(marker):
    for p in [Path(core.__file__), *sorted(ENGINE.rglob("*.py"))]:
        if marker in p.read_text():
            return str(p)


def widened(rel):
    return rel == "pkg/core.py" or rel.startswith("eng/")
'''
FIRST = "src/pkg/helper.py src/pkg/sub/fmt.py The formats\n    fmt\n"


def _two_batches(r: Path, files=None):
    """BASE: the first batch (pkg.helper's fmt into an existing package)
    done; HEAD: the second (TABLE's rows), with the first row in the table."""
    _repo(r, {MD.ARCH: STAND_IN, "scripts/architecture-baseline.json": BASELINE,
              "src/pkg/sub/__init__.py": '"""Sub."""\n', **(files or {})}, tools=True)
    _git(r, "init", "-q")
    trees = []
    for table in (FIRST, FIRST + TABLE):
        _write(r, MD.TABLE, table)
        run = subprocess.run([sys.executable, "-I", "-S", "-B", str(r / "scripts/move_defs.py"),
                              str(r / MD.TABLE), f"--repo={r}"], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        _git(r, "add", "-A")
        trees.append(_git(r, "write-tree").strip())
    return trees


class TestTheTables(unittest.TestCase):
    def test_two_sources_and_an_existing_package(self):
        table = ("src/pkg/core.py src/pkg/sub/x.py The x\n    _ordinal\n"
                 "src/pkg/model.py src/pkg/sub/items.py The items\n    Item\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"src/pkg/sub/__init__.py": '"""Sub."""\n'},
                                     table=table)
            self.assertEqual((r / "src/pkg/model.py").read_text(), (
                "from pkg.sub.items import Item\n\n" + MD.ALL_NOTE + '\n__all__ = ["Item"]\n\n\n'
                + MODEL.split("\n\n\nclass Item")[0] + "\n"))
            self.assertIn("from pkg.model import Item as Item  # re-export\n"
                          "from pkg.sub.x import _ordinal\n", (r / "src/pkg/core.py").read_text())
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("check: 2 row(s): src/pkg/sub/x.py, src/pkg/sub/items.py", out)

    def test_a_source_of_an_earlier_row_is_no_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _two_batches(r)
            self.assertIn("from pkg import helper as _H\n", (r / "src/pkg/eng/consts.py").read_text())
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)

    def test_the_moves_cycle_rule_reads_every_row(self):
        # An earlier row's module importing a new target back: refused, before
        # anything is written (the batch's rows alone see no cycle).
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/sub/__init__.py": '"""Sub."""\n'})
            rc, out = _run(r, FIRST)
            self.assertEqual(rc, 0, out)
            fmt = r / "src/pkg/sub/fmt.py"
            fmt.write_text(fmt.read_text() + "\n\ndef _late():\n    from pkg.eng import consts\n"
                           "    return consts\n")
            before = _files(r)
            rc, out = _run(r, FIRST + TABLE)
            self.assertEqual(rc, 1, out)
            self.assertIn("pkg.eng.consts imports pkg.helper, which imports pkg.eng.consts back "
                          "(through pkg.sub.fmt): a cycle", out)
            self.assertEqual(_files(r), {**before, MD.TABLE: (FIRST + TABLE).encode()})

    def test_an_earlier_batchs_names_count(self):
        spy = 'from unittest import mock\nP = mock.patch("pkg.helper.fmt")\n'
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _two_batches(r, {"tests/test_fmt.py": spy})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("check: seams: tests/test_fmt.py:2: names pkg.helper.fmt "
                          "(src/pkg/sub/fmt.py binds fmt)", out)

    def test_an_import_with_another_noqa_is_dropped(self):
        # Only a `noqa: F401` keeps an import (see the refusals): one with
        # another code that only the moved code reads goes, its noqa with it.
        core = CORE.replace("import functools\n", "import functools  # noqa: E402\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": core})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("functools", (r / "src/pkg/core.py").read_text())
            self.assertIn("drops List, Optional, Row, _H, _dc_field, dataclass, functools\n", out)

    def test_a_target_imports_plainly(self):
        core = CORE.replace("return _H.fmt(x) if x else _ZERO", "return _H.fmt(x) if Item else _ZERO")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": core})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            consts = (r / "src/pkg/eng/consts.py").read_text()
            self.assertIn("from pkg import helper as _H\n\nfrom pkg.model import Item\n", consts)
            self.assertIn("imports helper as _H, Item\n", out)
            self.assertIn("from pkg.model import Item as Item  # re-export\n",
                          (r / "src/pkg/core.py").read_text())

    def test_the_comments_left_are_listed(self):
        core = CORE.replace("# A candidate pair.\n", "# The candidates' section.\n# Two lines.\n\n"
                            "# A candidate pair.\n").replace("\n\ndef kept", "\n\n# The kept.\ndef kept")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": core})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            at = _line(core, "# The candidates' section.")
            self.assertIn(f"src/pkg/core.py: a comment stays with no statement after it (BASE's "
                          f"line(s) {at}): settle each by hand", out)
            self.assertIn("# Two lines.\n\n\n# The kept.\ndef kept", (r / "src/pkg/core.py").read_text())
        # A section a statement that stays still follows: not listed.
        core = core.replace('_Cand.__doc__ = """One candidate."""\n',
                            '_Cand.__doc__ = """One candidate."""\n_KEEP = 2\n')
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": core})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertNotIn("a comment stays", out)
        # A section an import dropped whole opens, the rest of it moved: listed.
        core3 = core.replace("import functools\n", "").replace(
            "from pkg.model import Item as Item  # re-export\n",
            "from pkg.model import Item as Item  # re-export\n# The cache's tools.\n\n"
            "import functools\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            src = core3.replace("_KEEP = 2\n", "")
            _repo(r, {"src/pkg/core.py": src})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            at, at2 = (_line(src, x) for x in ("# The cache's tools.", "# The candidates' section."))
            self.assertIn(f"(BASE's line(s) {at}, {at2}): settle each by hand", out)
        # A comment above an import dropped whole: listed once, at its first line.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"src/pkg/core.py": CORE.replace("import functools\n",
                                                      "# Caching.\n# Two.\nimport functools\n")})
            rc, out = _run(r)
            self.assertEqual(rc, 0, out)
            self.assertIn("src/pkg/core.py: a comment stays with no statement after it (BASE's "
                          "line(s) 2): settle each by hand", out)


class TestTheRealArchitectureCheck(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("ruff"), "needs ruff (the dev venv's)")
    def test_an_import_only_moved_code_read(self):
        # Kept only for a `noqa: F401`; dropped otherwise, its hit or noqa
        # with it: a total that shrinks passes.
        table = ("src/taxjson/lib/mix.py src/taxjson/engine/__init__.py The engines\n"
                 "src/taxjson/lib/mix.py src/taxjson/engine/consts.py The figures\n    _shown\n")
        for marker, gone in (("  # noqa: E402", True), ("", True)):
            core = (f"X = 1\nimport json{marker}\n\n\ndef _shown(x):\n    return json.dumps(x)\n"
                    "\n\ndef kept(x):\n    return _shown(x) + str(X)\n")
            with self.subTest(marker or "a live E402"), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                rc, out, check = _real(r, core, table)
                self.assertEqual(rc, 0, out)
                self.assertEqual("import json" not in (r / "src/taxjson/lib/mix.py").read_text(),
                                 gone)
                self.assertEqual(check[0], 0, check[1])

    @unittest.skipUnless(importlib.util.find_spec("ruff"), "needs ruff (the dev venv's)")
    def test_a_move_of_a_print_and_a_noqa(self):
        core = ("# A figure, printed for the trace.\ndef _shown(x):\n    print(x)\n"
                "    f = lambda y: y  # noqa: E731\n    return f(x)\n\n\n"
                "def kept(x):\n    return _shown(x) + 1\n")
        table = ("src/taxjson/lib/mix.py src/taxjson/engine/__init__.py The engines\n"
                 "src/taxjson/lib/mix.py src/taxjson/engine/consts.py The figures\n    _shown\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            for rel, text in {"src/taxjson/__init__.py": "", "src/taxjson/lib/__init__.py": "",
                              "src/taxjson/lib/mix.py": core,
                              "pyproject.toml": '[tool.ruff.lint]\nselect = ["E4", "E7", "E9", '
                                                '"F", "BLE001"]\n'}.items():
                _write(r, rel, text)
            for x in MD.TOOLS + (MD.ARCH,):
                (r / x).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_paths.REPO / x, r / x)
            _git(r, "init", "-q")
            rc, text = MD._arch(r, ["--accept-growth=all"])
            self.assertEqual(rc, 0, text)
            _git(r, "add", "-A")
            base = _git(r, "write-tree").strip()
            _write(r, MD.TABLE, table)
            run = subprocess.run([sys.executable, "-I", "-S", "-B", str(r / "scripts/move_defs.py"),
                                  str(r / MD.TABLE), f"--repo={r}"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("the architecture baseline recorded", run.stdout)
            doc = json.loads((r / MD.BASELINE).read_text())
            self.assertEqual(doc["print_calls"]["print"], {"src/taxjson/engine/consts.py": 1})
            self.assertEqual(doc["noqa"]["E731"], {"src/taxjson/engine/consts.py": 1})
            _git(r, "add", "-A")
            rc, out = _check(r, base, _git(r, "write-tree").strip())
            self.assertEqual(rc, 0, out)


def _real(r: Path, core: str, table: str):
    """A small copy with the real check_architecture: its baseline recorded,
    the move, then --check. (the move's rc, its output, (--check's rc, its
    output))."""
    for rel, text in {"src/taxjson/__init__.py": "", "src/taxjson/lib/__init__.py": "",
                      "src/taxjson/lib/mix.py": core,
                      "pyproject.toml": '[tool.ruff.lint]\nselect = ["E4", "E7", "E9", "F", '
                                        '"BLE001"]\n'}.items():
        _write(r, rel, text)
    for x in MD.TOOLS + (MD.ARCH,):
        (r / x).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_paths.REPO / x, r / x)
    _git(r, "init", "-q")
    rc, text = MD._arch(r, ["--accept-growth=all"])
    assert rc == 0, text
    _git(r, "add", "-A")
    base = _git(r, "write-tree").strip()
    _write(r, MD.TABLE, table)
    run = subprocess.run([sys.executable, "-I", "-S", "-B", str(r / "scripts/move_defs.py"),
                          str(r / MD.TABLE), f"--repo={r}"], capture_output=True, text=True)
    if run.returncode:
        return run.returncode, run.stdout + run.stderr, (None, "")
    _git(r, "add", "-A")
    return 0, run.stdout, _check(r, base, _git(r, "write-tree").strip())


class TestTheBaseline(unittest.TestCase):
    B = {"_": "x", "ruff": {"E741": {"src/pkg/core.py": 4, "src/pkg/helper.py": 1}},
         "function_length": {"src/pkg/core.py::_Cand.first": [160],
                             "src/pkg/core.py::kept": [151]}}
    H = {"_": "x", "ruff": {"E741": {"src/pkg/core.py": 1, "src/pkg/eng/consts.py": 1,
                                     "src/pkg/eng/ca/cand.py": 2, "src/pkg/helper.py": 1}},
         "function_length": {"src/pkg/eng/ca/cand.py::_Cand.first": [160],
                             "src/pkg/core.py::kept": [151]}}

    def _problems(self, head: dict, base: dict = None):
        with tempfile.TemporaryDirectory() as td:
            b, h = Path(td) / "b", Path(td) / "h"
            for d, doc in ((b, base or self.B), (h, head)):
                _write(d, MD.BASELINE, json.dumps(doc))
            return MD.baseline_problems(b, h, MD.read_table(TABLE))

    def test_what_moves_with_its_code_passes(self):
        self.assertEqual(self._problems(self.H), [])
        # A total that shrinks (what the move dropped took its hit with it).
        shrunk = json.loads(json.dumps(self.H))
        shrunk["ruff"]["E741"]["src/pkg/eng/ca/cand.py"] = 1
        self.assertEqual(self._problems(shrunk), [])

    def test_what_fails(self):
        def edit(f):
            doc = json.loads(json.dumps(self.H))
            f(doc)
            return doc
        cases = [
            (edit(lambda d: d["ruff"]["E741"].update({"src/pkg/eng/ca/cand.py": 3})),
             "baseline: ruff E741: 6 in HEAD, 5 in BASE"),
            (edit(lambda d: d["ruff"]["E741"].update({"src/pkg/core.py": 0,
                                                      "src/pkg/helper.py": 2})),
             "baseline: ruff E741 src/pkg/helper.py: 2 in HEAD, 1 in BASE"),
            (edit(lambda d: d.update(noqa={"E731": {"src/pkg/core.py": 1}})),
             "baseline: noqa E731: 1 in HEAD, 0 in BASE"),
            (edit(lambda d: d["function_length"].update({"src/pkg/eng/ca/cand.py::_Cand.first": [161]})),
             "baseline: function_length src/pkg/eng/ca/cand.py::_Cand.first: [161] in HEAD, "
             "[160] in BASE, re-keyed"),
            (edit(lambda d: d["function_length"].update({"src/pkg/core.py::_Cand.first": [160]})),
             "baseline: function_length src/pkg/core.py::_Cand.first: [160] in HEAD, None in BASE"),
            (edit(lambda d: d.update(_="y")), "baseline: _ is not BASE's"),
        ]
        for doc, why in cases:
            with self.subTest(why):
                got = self._problems(doc)
                self.assertTrue(any(why in p for p in got), got)


if __name__ == "__main__":
    unittest.main()

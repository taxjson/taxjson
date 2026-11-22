"""scripts/extract_support.py (PLAN.md step 9 B1): what test modules
share moves to tests/_support/, on a synthetic repository.

- A source's imported names and the module-level names they reach move,
  their text and the comments above them kept, with the imports they
  use; the source imports back what it still reads and drops what only
  they read; each importer imports from the support module; the ratchet
  is the new count; line ends are kept; the extracted tests still run.
- Each source the tool cannot do whole is refused before anything is
  written (a class that runs tests where it is bound; a fixture base or
  a mixin moves as it is); a source that needs another's names waits
  for it.
- The scope reading: a function's own names are not the module's.
- --check: BASE's tool, run on BASE, gives HEAD's tree, every path, mode
  and blob of it; anything else fails (an edit, a tool change, a file
  `.gitattributes` hides from `git archive`, a .pyc, a mode, a symlink, a
  submodule, a baseline count, a header not the tool's); a batch that
  fails has none of its code run; BASE's copy is what runs; the ids,
  each with its markers and skip."""
import ast
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path

import _paths


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", _paths.REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ES = _load("extract_support")

SRC = '''"""Source tests."""
import json
import os
import unittest

HEAD = "a,b\\n"
OTHER = 1


# The row helper.
def _row(x):
    return HEAD + json.dumps(x)


def _unused():
    return os.sep


class TestSrc(unittest.TestCase):
    def test_row(self):
        self.assertTrue(_row(1).startswith(HEAD))
'''
USER = '''"""User tests."""
import unittest
from test_src import _row


class TestUser(unittest.TestCase):
    def test_it(self):
        self.assertEqual(_row(2), "a,b\\n2")
'''
LAYOUT = '''import ast
CROSS_IMPORTS_MAX = 1


def cross_imports(path, stems):
    tree = ast.parse(path.read_text())
    return [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            and n.module in stems and n.module != path.stem]
'''
SUPPORT = '''"""Shared test helpers: the rows.

Shared from: test_src
"""
import json


HEAD = "a,b\\n"


# The row helper.
def _row(x):
    return HEAD + json.dumps(x)
'''
SRC_AFTER = '''"""Source tests."""
import os
import unittest
from _support.sup import HEAD, _row

OTHER = 1


def _unused():
    return os.sep


class TestSrc(unittest.TestCase):
    def test_row(self):
        self.assertTrue(_row(1).startswith(HEAD))
'''
TABLE = [("test_src", "sup", "the rows")]


def _git(r: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=r, check=True, capture_output=True,
                          text=True).stdout


def _repo(r: Path, files=None, tools=False) -> None:
    files = {"tests/test_src.py": SRC, "tests/test_user.py": USER,
             "tests/test_test_layout.py": LAYOUT, **(files or {})}
    for rel, text in files.items():
        if text is None:
            continue
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        with open(r / rel, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    if tools:
        for t in ES.TOOLS:
            (r / t).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_paths.REPO / t, r / t)


def _run(r: Path, table=TABLE, dry_run=False):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ES.extract(r, table, dry_run)
    return rc, out.getvalue()


class TestTheExtraction(unittest.TestCase):
    def test_the_support_import_where_a_dropped_helper_was(self):
        # TJ-3845: what a dropped helper ran first (the hermetic setup)
        # runs at the same point, through _support.
        head = '"""Source."""\nimport os\nimport unittest\nimport _style\n'
        tail = ('\n\ndef helper():\n    return _style.X\n\n\nclass TestSrc(unittest.TestCase):\n'
                '    def test_t(self):\n        self.assertTrue(os.sep%s)\n')
        style = {"tests/_style.py": "import _hermetic  # noqa: F401\n\nX = 1\n",
                 "src/plug/__init__.py": "", "src/plug/cfg.py": "HOME = 1\n"}
        alone = "import _support  # noqa: F401  (the hermetic setup, as the dropped imports gave it)\n"
        cases = [
            ("imports back, before a later import", head + "from plug import cfg\n" + tail % " and helper() and cfg",
             True, "import unittest\nfrom _support.sup import helper\nfrom plug import cfg\n"),
            ("imports nothing back", head + tail % "", True, "import unittest\n" + alone + "\n\nclass"),
            ("no hermetic setup in the tree", head + tail % "", False, "import unittest\n\n\nclass"),
            ("a library's drop above a helper's",
             '"""Source."""\nimport json\nimport os\nimport unittest\nimport _style\n\n\ndef helper():\n'
             '    return json.dumps(_style.X)\n\n\nclass TestSrc(unittest.TestCase):\n    def test_t(self):\n'
             '        self.assertTrue(os.sep and helper())\n', True,
             "import os\nimport unittest\nfrom _support.sup import helper\n\n\nclass"),
            ("only a library's import dropped",
             '"""Source."""\nimport json\nimport os\nimport unittest\n\n\ndef helper():\n'
             '    return json.dumps(1)\n\n\nclass TestSrc(unittest.TestCase):\n    def test_t(self):\n'
             '        self.assertTrue(os.sep and helper())\n', True,
             "import os\nimport unittest\nfrom _support.sup import helper\n"),
            ("nothing comes back, no helper dropped",
             '"""Source."""\nimport json\nimport unittest\n\n\ndef helper():\n'
             '    return json.dumps(1)\n\n\nclass TestSrc(unittest.TestCase):\n    def test_t(self):\n'
             '        self.assertTrue(1)\n', True, "import unittest\n\n\nclass TestSrc"),
        ]
        for why, src, herm, want in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, {"tests/test_src.py": src, "tests/test_user.py": "from test_src import helper\n",
                          **style, **({"tests/_hermetic.py": ""} if herm else {})})
                self.assertEqual(_run(r)[0], 0)
                self.assertIn(want, (r / "tests/test_src.py").read_text())
        # A helper that is a package.
        pkg = {"tests/_style/__init__.py": style["tests/_style.py"], "tests/_style.py": None}
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": cases[0][1], "tests/test_user.py": "from test_src import helper\n",
                      **style, **pkg, "tests/_hermetic.py": ""})
            self.assertEqual(_run(r)[0], 0)
            self.assertIn(cases[0][3], (r / "tests/test_src.py").read_text())

    def test_an_existing_support_init_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/_support/__init__.py": "# kept\n", "tests/_hermetic.py": ""})
            _run(r)
            self.assertEqual((r / "tests/_support/__init__.py").read_text(), "# kept\n")

    def test_a_source_on_a_synthetic_repository(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            rc, out = _run(r)
            self.assertEqual(rc, 0)
            self.assertEqual((r / "tests/_support/sup.py").read_text(), SUPPORT)
            self.assertEqual((r / "tests/test_src.py").read_text(), SRC_AFTER)
            self.assertEqual((r / "tests/test_user.py").read_text(),
                             USER.replace("from test_src import", "from _support.sup import"))
            self.assertEqual((r / "tests/_support/__init__.py").read_text(), ES.INIT_DOC)
            self.assertIn("CROSS_IMPORTS_MAX = 0\n", (r / "tests/test_test_layout.py").read_text())
            self.assertIn("test_src -> tests/_support/sup.py: 2 name(s) in 2 statement(s), "
                          "1 importing module(s); test_src imports back 2", out)
            # The extracted modules still run.
            run = subprocess.run([sys.executable, "-m", "unittest", "test_src", "test_user"],
                                 cwd=r / "tests", capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("Ran 2 tests", run.stderr)

    def test_a_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            rc, out = _run(r, dry_run=True)
            self.assertEqual(rc, 0)
            self.assertIn("2 name(s)", out)
            self.assertFalse((r / "tests/_support").exists())
            self.assertEqual((r / "tests/test_src.py").read_text(), SRC)

    def test_line_ends_are_kept(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            crlf = {k: v.replace("\n", "\r\n") for k, v in
                    (("tests/test_src.py", SRC), ("tests/test_user.py", USER))}
            _repo(r, crlf)
            _run(r)
            for f, want in (("tests/_support/sup.py", SUPPORT), ("tests/test_src.py", SRC_AFTER)):
                with open(r / f, encoding="utf-8", newline="") as h:
                    self.assertEqual(h.read(), want.replace("\n", "\r\n"), f)

    def test_a_sections_banner_stays(self):
        # TJ-3799: a banner (`# -----`) is its section's, not the helper's.
        src = SRC.replace("# The row helper.\n", "# ------------------------------ rows\n"
                                                  "# The row helper.\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": src})
            _run(r)
            self.assertNotIn("------ rows", (r / "tests/_support/sup.py").read_text())
            self.assertIn("# The row helper.\ndef _row", (r / "tests/_support/sup.py").read_text())
            # TJ-3826: directly above what stays, as it was above the helper.
            self.assertIn("\n\n\n# ------------------------------ rows\ndef _unused():",
                          (r / "tests/test_src.py").read_text())
        # TJ-3839: three cuts in a row below a banner are one.
        three = SRC.replace("# The row helper.\n", "# ------------------------------ rows\n"
                                                    "X1 = 1\n\n\nX2 = 2\n\n\n# The row helper.\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": three,
                      "tests/test_user.py": USER.replace("from test_src import _row",
                                                         "from test_src import X1, X2, _row")})
            _run(r)
            self.assertIn("\n\n\n# ------------------------------ rows\ndef _unused():",
                          (r / "tests/test_src.py").read_text())
        # A blank line between the banner and the cut: the gap stays.
        gap = SRC.replace("# The row helper.\n", "# ------------------------------ rows\n\n"
                                                  "# The row helper.\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": gap})
            _run(r)
            self.assertIn("# ------------------------------ rows\n\n\ndef _unused():",
                          (r / "tests/test_src.py").read_text())
        # A banner over an empty section keeps the blank lines below it.
        src2 = src.replace("def _unused():", "# ------------------------------ more\n"
                                             "def _unused():")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": src2})
            _run(r)
            self.assertIn("# ------------------------------ rows\n\n\n# ---",
                          (r / "tests/test_src.py").read_text())

    def test_an_import_inside_a_function_and_one_wrapped(self):
        user = ('import unittest\n\n\nclass T(unittest.TestCase):\n'
                '    def test_t(self):\n'
                '        from test_src import (HEAD,\n'
                '                              _row as r)  # the helpers\n'
                '        self.assertTrue(r(1).startswith(HEAD))\n')
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_user.py": user})
            _run(r)
            self.assertIn("        from _support.sup import HEAD, _row as r  # the helpers\n"
                          "        self.assertTrue", (r / "tests/test_user.py").read_text())
        names = [ast.alias(name=f"a_long_helper_name_{i}") for i in range(8)]
        self.assertEqual(ES.from_line("    ", "_support.x", names),
                         "    from _support.x import (\n"
                         "        a_long_helper_name_0, a_long_helper_name_1, "
                         "a_long_helper_name_2, a_long_helper_name_3,\n"
                         "        a_long_helper_name_4, a_long_helper_name_5, "
                         "a_long_helper_name_6, a_long_helper_name_7,\n"
                         "    )")

    def test_a_source_that_needs_another_waits_for_it(self):
        files = {"tests/test_a.py": '"""A."""\nfrom test_b import B\n\n\ndef fa():\n'
                                    '    return B\n',
                 "tests/test_b.py": '"""B."""\nB = 2\n',
                 "tests/test_user.py": "from test_a import fa\nfrom test_b import B\n"}
        table = [("test_a", "a", "the a rows"), ("test_b", "b", "the b rows")]
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, files)
            rc, _out = _run(r, table)
            self.assertEqual(rc, 0)
            self.assertEqual((r / "tests/_support/a.py").read_text(),
                             '"""Shared test helpers: the a rows.\n\nShared from: test_a\n"""\n'
                             'from _support.b import B\n\n\ndef fa():\n    return B\n')
            self.assertEqual((r / "tests/test_user.py").read_text(),
                             "from _support.a import fa\nfrom _support.b import B\n")
            self.assertEqual((r / "tests/test_a.py").read_text(), '"""A."""\n')
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, files)
            with self.assertRaisesRegex(ES.Refused, "the test module test_b: extract that first"):
                _run(r, table[:1])


HEADER = '"""Source tests."""\nimport os\nimport sys\nimport unittest\n'
# An importer that may import the helper _cfg in a test.
LATER = "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n"
# The same, and a test that sets TAXJSON_DEV_DIR (TJ-3989: a variable the
# tests only read is no test's).
LATER_DEV = "import os\n" + LATER + "\n\ndef t(d):\n    os.environ['TAXJSON_DEV_DIR'] = d\n"
# The product's package, and a module with one of its cache wrappers (CACHED).
TAXJSON = {"src/taxjson/__init__.py": "", "src/taxjson/lib/__init__.py": ""}
MARKETS = ("import functools\n\n\n" "@functools.lru_cache(maxsize=None)\ndef canadian_suffixes():\n"
           "    return frozenset({'TO', 'V'})\n\n\ndef other():\n    return 1\n")


def _constants(cs):
    """An importer that may import _cfg in a test, and tests that give
    TJ_K_STATE each of CS."""
    return "import os\n" + LATER + "".join(f"\n\ndef t_{c}():\n    os.environ['TJ_K_STATE'] = '{c}'\n"
                                           for c in cs)
# A helper whose state is a file's below HOME, read at import (TJ-3904).
READ_HOME_FILE = ("import os\n\n\ndef _read(p):\n    try:\n        with open(p, encoding='utf-8') as f:\n"
                  "            return f.read()\n    except OSError:\n        return ''\n\n\n"
                  "CFG = _read(os.path.expanduser('~/.plugcfg'))\n")
# A package's test bootstrap, as taxjson-fetch's: the core's tests/ last on
# the path (TJ-3905).
BOOTSTRAP = ("import os\nimport sys\n\nsys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "
             "'..', '..', '..', 'tests'))\n")
# A setup that serves plug.cfg under its own HOME whatever a test patches in,
# as tests/_hermetic's CacheHome serves the two caches (TJ-3904).
PINNED_SETUP = (
    "import importlib.machinery\nimport os\nimport sys\nimport tempfile\n\n"
    "HOME = tempfile.mkdtemp()\nos.environ['HOME'] = HOME\n\n\n"
    "class _Under:\n    def __init__(self, loader):\n        self.loader = loader\n\n"
    "    def create_module(self, spec):\n        return self.loader.create_module(spec)\n\n"
    "    def exec_module(self, m):\n        old = os.environ['HOME']\n        os.environ['HOME'] = HOME\n"
    "        try:\n            self.loader.exec_module(m)\n        finally:\n            os.environ['HOME'] = old\n\n\n"
    "class CacheHome:\n    def find_spec(self, name, path=None, target=None):\n"
    "        if name != 'plug.cfg':\n            return None\n"
    "        spec = importlib.machinery.PathFinder.find_spec(name, path)\n"
    "        if spec is not None:\n            spec.loader = _Under(spec.loader)\n        return spec\n\n\n"
    "sys.meta_path.insert(0, CacheHome())\n")
REFUSALS = [
    # (what, the source's code after HEADER, the user's lines, the refusal)
    ("a re-export", "from json import dumps\n", "from test_src import dumps",
     "no module-level def, class or assignment of its"),
    ("bound twice", "A = 1\nA = 2\n", "from test_src import A", "A is bound at module level twice"),
    ("bound in an if", "A = 1\nif os.sep:\n    A = 2\n", "from test_src import A",
     "also at line 6"),
    ("by global", "A = 1\n\n\ndef f():\n    global A\n    A = 2\n", "from test_src import A",
     "through `global`"),
    ("a TestCase", "class TestA(unittest.TestCase):\n    def test_a(self):\n        pass\n",
     "from test_src import TestA", "TestA is a TestCase class with tests: they would run"),
    # TJ-3946: a class that runs tests where it is bound, by each way.
    ("a TestCase on a mixin of SOURCE's", "class Cases:\n    def test_a(self):\n        pass\n\n\n"
                                          "class Both(Cases, unittest.TestCase):\n    pass\n",
     "from test_src import Both", "Both is a TestCase class with tests:"),
    ("a TestCase's subclass", "class _A(unittest.TestCase):\n    def setUp(self):\n        pass\n\n\n"
                              "class _B(_A):\n    pass\n\n\nclass C(_B):\n    def test_c(self):\n        pass\n",
     "from test_src import C", "C is a TestCase class with tests:"),
    ("a runTest", "class R(unittest.TestCase):\n    def runTest(self):\n        pass\n",
     "from test_src import R", "R is a TestCase class with tests:"),
    ("a test bound in the class", "def _t(self):\n    pass\n\n\nclass R(unittest.TestCase):\n    test_x = _t\n",
     "from test_src import R", "R is a TestCase class with tests:"),
    ("a TestCase on a base from elsewhere", "from parser_x import Cases\n\n\n"
                                            "class Both(Cases, unittest.TestCase):\n    pass\n",
     "from test_src import Both", "Both is a TestCase class on a base from elsewhere (its tests unread here):"),
    ("tests on a base from elsewhere", "from _support.x import _Base\n\n\n"
                                       "class T(_Base):\n    def test_t(self):\n        pass\n",
     "from test_src import T", "T is a class with tests on a base from elsewhere:"),
    ("two names, one moves", "A, B = 1, 2\n", "from test_src import A",
     "binds B with A: split it by hand"),
    ("a line shared", "A = 1; B = 2\n", "from test_src import A",
     "holds a statement that moves and another"),
    ("reads __file__", "A = __file__\n", "from test_src import A", "reads where it runs (__file__)"),
    ("reads sys.modules", "def f():\n    return sys.modules[__name__]\n", "from test_src import f",
     "sys.modules[__name__]): it would read"),
    ("its sys.modules entry by get", "def f():\n    return sys.modules.get('test_src')\n",
     "from test_src import f", "reads where it runs (sys.modules[__name__])"),
    ("vars()", "def f():\n    return vars()\n", "from test_src import f", "reads where it runs (vars())"),
    ("locals()", "def f():\n    return locals()\n", "from test_src import f",
     "reads where it runs (locals())"),
    ("globals by alias", "def f():\n    g = globals\n    return g\n", "from test_src import f",
     "reads where it runs (globals)"),
    ("exec", "def f(s):\n    exec(s)\n", "from test_src import f", "reads where it runs (exec)"),
    ("__spec__", "def f():\n    return __spec__\n", "from test_src import f",
     "reads where it runs (__spec__)"),
    ("a frame's caller", "def f(fr):\n    return fr.f_back\n", "from test_src import f",
     "reads where it runs (f_back)"),
    ("a function's globals", "def f(g):\n    return g.__globals__\n", "from test_src import f",
     "reads where it runs (__globals__)"),
    # TJ-3828
    ("an object's module", "def f(o):\n    return o.__module__\n", "from test_src import f",
     "reads where it runs (__module__)"),
    ("an object's module by a string (TJ-4006)", "def f(o):\n    return getattr(o, '__module__')\n",
     "from test_src import f", "reads where it runs (__module__)"),
    ("the stack", "import traceback\n\n\ndef f():\n    return traceback.extract_stack()\n",
     "from test_src import f", "reads where it runs (traceback.extract_stack)"),
    ("a location reader imported", "from inspect import getsourcefile\n\n\ndef f():\n"
                                   "    return getsourcefile(f)\n",
     "from test_src import f", "reads where it runs (getsourcefile)"),
    # TJ-3820: code that stays, its own namespace by name.
    ("stays and reads its own entry", "A = 1\nK = sys.modules[__name__]\n", "from test_src import A",
     "reads its own module's names by name (sys.modules[__name__])"),
    ("stays and reads vars()", "A = 1\n\n\ndef g():\n    return vars()\n", "from test_src import A",
     "reads its own module's names by name (vars())"),
    # Round two's mutants (TJ-3825): the hooks, del and global, a call that
    # changes an import, an if or try that runs code, a keyword default.
    ("tearDownModule", "def tearDownModule():\n    pass\n", "from test_src import tearDownModule",
     "tearDownModule would move"),
    ("load_tests", "def load_tests(*a):\n    pass\n", "from test_src import load_tests",
     "load_tests would move"),
    ("del at import", "ROWS = [1, 2]\ndel ROWS[0]\n", "from test_src import ROWS",
     "runs code at import (Delete)"),
    ("del in a def called at import", "ROWS = [1, 2]\n\n\ndef _drop():\n    del ROWS[0]\n\n\n"
                                      "K = _drop()\n",
     "from test_src import ROWS", "calls _drop at import, which changes module-level state "
                                 "(ROWS stored into)"),
    ("global in a def called at import", "A = 1\nN = 0\n\n\ndef bump():\n    global N\n"
                                         "    N = 1\n\n\nK = bump()\n",
     "from test_src import A", "calls bump at import, which changes module-level state (global N)"),
    ("a def that changes an import", "A = 1\n\n\ndef _env():\n    os.environ['X_TJ'] = '1'\n\n\n"
                                     "K = _env()\n",
     "from test_src import A", "calls _env at import, which changes module-level state (os stored into)"),
    ("an if that runs code", "A = 1\nif os.sep:\n    print(A)\n", "from test_src import A",
     "runs code at import (If)"),
    ("a try that runs code", "A = 1\ntry:\n    print(A)\nexcept OSError:\n    pass\n",
     "from test_src import A", "runs code at import (Try)"),
    ("a keyword default", "ROWS = []\n\n\ndef _fill(r):\n    r.append(1)\n    return r\n\n\n"
                          "def kept(*, r=_fill(ROWS)):\n    return r\n",
     "from test_src import ROWS", "passes ROWS at import to _fill"),
    ("needs a test module", "from test_other import X\n\n\ndef f():\n    return X\n",
     "from test_src import f", "the test module test_other"),
    ("code runs first", "sys.path.insert(0, 'x')\nimport json\n\n\ndef f():\n    return json\n",
     "from test_src import f", "runs code at import (Expr)"),
    # TJ-3790: an importer of the moved names would not run it.
    ("a side effect at import", "A = 1\nos.environ.setdefault('X', '1')\n",
     "from test_src import A", "test_src:6 runs code at import (Expr)"),
    ("a loop at import", "A = []\nfor i in range(2):\n    pass\n", "from test_src import A",
     "runs code at import (For)"),
    # TJ-3790, TJ-3794: what stays reads a moved name at import.
    ("fills a moved name", "CASES = []\n\n\ndef case(f):\n    CASES.append(f)\n    return f\n\n\n"
                           "@case\ndef stays():\n    pass\n",
     "from test_src import CASES, case", "calls case at import, which changes module-level "
                                         "state (CASES.append())"),
    ("a helper that stays fills it", "REG = {}\n\n\ndef reg(k):\n    REG[k] = 1\n    return k\n\n\n"
                                     "K = reg('a')\n",
     "from test_src import REG", "calls reg at import, which changes module-level state "
                                 "(REG stored into)"),
    ("through another helper", "REG = {}\n\n\ndef _put(k):\n    REG[k] = 1\n\n\ndef reg(k):\n"
                               "    _put(k)\n    return k\n\n\nK = reg('a')\n",
     "from test_src import REG", "(REG stored into, through _put)"),
    ("passed to a def that fills it", "CASES = []\n\n\ndef _put(lst):\n    lst.append(1)\n\n\n"
                                      "K = _put(lst=CASES)\n",
     "from test_src import CASES", "passes CASES at import to _put, which can change it"),
    ("passed to an imported function", "ROWS = []\nK = os.fspath(ROWS)\n",
     "from test_src import ROWS", "passes ROWS at import to os, which can change it"),
    ("a tuple that holds a list", "ROWS = ('a', [])\nK = os.fspath(ROWS)\n",
     "from test_src import ROWS", "passes ROWS at import to os, which can change it"),
    ("passed to a class that fills it", "ROWS = []\n\n\nclass Reg:\n    def __init__(self, r):\n"
                                        "        r.append(self)\n\n\nK = Reg(ROWS)\n",
     "from test_src import ROWS", "passes ROWS at import to Reg"),
    ("a store at import", "ROWS = {}\nROWS['k'] = 1\n", "from test_src import ROWS",
     "changes at import what an importer of the moved names would read (ROWS stored into)"),
    ("an environment at import", "A = 1\nos.environ['X'] = '1'\n", "from test_src import A",
     "(os stored into)"),
    ("read above its definition", "X = open\n\n\ndef open():\n    return 1\n",
     "from test_src import open", "reads open at import above its definition"),
    # TJ-3791
    ("a module hook", "def setUpModule():\n    pass\n", "from test_src import setUpModule",
     "setUpModule would move: unittest's module hooks stay"),
    # TJ-3792, TJ-3798: each name that reads where code is.
    ("reads __name__", "def f():\n    return __name__\n", "from test_src import f",
     "reads where it runs (__name__)"),
    ("reads globals", "def f():\n    return globals()\n", "from test_src import f",
     "reads where it runs (globals)"),
    ("reads __doc__", "def f():\n    return __doc__\n", "from test_src import f",
     "reads where it runs (__doc__)"),
    ("eval", "def f(s):\n    return eval(s)\n", "from test_src import f", "reads where it runs (eval)"),
    ("inspect by another name", "import inspect as ins\n\n\ndef f():\n    return ins.getsourcefile(f)\n",
     "from test_src import f", "reads where it runs (inspect.getsourcefile)"),
    # TJ-3834
    ("its own patch target by import_module", "import importlib\nfrom unittest import mock\n\nA = 1\n\n\n"
     "def g():\n    return mock.patch.object(importlib.import_module(__name__), 'A', 2)\n",
     "from test_src import A", "reads its own module's names by name (import_module(__name__))"),
    ("its own patch target by a bare import_module", "from importlib import import_module\n"
     "from unittest import mock\n\nA = 1\n\n\ndef g():\n"
     "    return mock.patch.object(import_module(__name__), 'A', 2)\n",
     "from test_src import A", "reads its own module's names by name (import_module(__name__))"),
    # TJ-3871
    ("its own module by __import__", "from unittest import mock\n\nA = 1\n\n\ndef g():\n"
     "    return mock.patch.object(__import__(__name__), 'A', 2)\n",
     "from test_src import A", "reads its own module's names by name (import_module(__name__))"),
    ("its own globals through a frame", "import sys\nfrom unittest import mock\n\nA = 1\n\n\ndef g():\n"
     "    return mock.patch.dict(sys._getframe().f_globals, {'A': 2})\n",
     "from test_src import A", "reads its own module's names by name (a frame's f_globals)"),
    # TJ-3911: a function's globals and getmodule hand over the module too.
    ("its own globals through a function", "A = 1\n\n\ndef _kept():\n    return 1\n\n\ndef g():\n"
     "    return _kept.__globals__['A']\n",
     "from test_src import A", "reads its own module's names by name (a function's __globals__)"),
    ("its own globals by getattr", "A = 1\n\n\ndef _kept():\n    return 1\n\n\ndef g():\n"
     "    return getattr(_kept, '__globals__')['A']\n",
     "from test_src import A", "reads its own module's names by name (a function's __globals__)"),
    ("its own module through inspect.getmodule", "import inspect\n\nA = 1\n\n\nclass K:\n"
     "    def a(self):\n        return inspect.getmodule(K).A\n",
     "from test_src import A", "reads its own module's names by name (inspect.getmodule)"),
    ("its own module through getmodule imported", "from inspect import getmodule\n\nA = 1\n\n\n"
     "def g():\n    return getmodule(g).A\n",
     "from test_src import A", "reads its own module's names by name (inspect.getmodule)"),
    ("its own entry through an alias of sys", "import sys as _s\n\nA = 1\n\n\ndef g():\n"
     "    return _s.modules[__name__]\n", "from test_src import A",
     "reads its own module's names by name (sys.modules[__name__])"),
    ("its own entry through modules", "from sys import modules\n\nA = 1\n\n\ndef g():\n"
     "    return modules[__name__]\n", "from test_src import A",
     "reads its own module's names by name (sys.modules[__name__])"),
    ("inspect", "import inspect\n\n\ndef f():\n    return inspect.getsourcefile(f)\n",
     "from test_src import f", "reads where it runs (inspect.getsourcefile)"),
    ("its own code", "def f():\n    return f.__code__.co_filename\n", "from test_src import f",
     "reads where it runs (__code__, co_filename)"),
    ("a frame", "def f():\n    return sys._getframe(0)\n", "from test_src import f",
     "reads where it runs (_getframe)"),
    # TJ-3795
    ("a star import in it", "from os.path import *\nA = 1\n", "from test_src import A",
     "star-imports os.path"),
    # TJ-3793: a patch target in its own terms.
    ("its own patch target", "A = 1\n\n\ndef g():\n    return f'{__name__}.A'\n",
     "from test_src import A", "it reads `__name__` (or a `.__module__`) and a string of it ends with `.A`"),
    # TJ-3821: the other spellings.
    ("its own target by %", "A = 1\n\n\ndef g():\n    return '%s.A' % __name__\n",
     "from test_src import A", "a string of it ends with `.A`"),
    ("its own target by !s", "A = 1\n\n\ndef g():\n    return f'{__name__!s}.A'\n",
     "from test_src import A", "a string of it ends with `.A`"),
    ("its own target by an alias", "A = 1\nME = __name__\n\n\ndef g():\n    return f'{ME}.A'\n",
     "from test_src import A", "a string of it ends with `.A`"),
    # TJ-3871
    ("its own target by a __module__", "A = 1\n\n\nclass K:\n    def g(self):\n"
     "        return f'{type(self).__module__}.A'\n", "from test_src import A",
     "a string of it ends with `.A`"),
    ("it imports itself", "import test_src as me\n\nA = 1\n", "from test_src import A",
     "imports itself"),
    ("it imports itself, unimported", "import test_src as me\n\nA = 1\n", "X = 1",
     "no test module imports from it"),
    ("it imports from itself", "A = 1\n\n\ndef g():\n    from test_src import A as a\n    return a\n",
     "from test_src import A", "imports itself"),
    ("patch.multiple", "A = 1\n", "from test_src import A\nP = mock.patch.multiple('test_src', A=2)",
     "names it and A in one call, and A moves"),
    ("patch.object by import_module", "A = 1\n",
     "from test_src import A\nP = mock.patch.object(importlib.import_module('test_src'), 'A')",
     "names it and A in one call"),
    # TJ-3818: a bare file name joined to a path, or passed to a call.
    ("a bare name joined", "A = 1\n", "from test_src import A\nP = _paths.TESTS / 'test_src.py'",
     "names its file in a string"),
    ("a bare name opened", "A = 1\n", "from test_src import A\nP = open('test_src.py')",
     "names its file in a string"),
    ("a relative import", "from .x import y\n\n\ndef f():\n    return y\n", "from test_src import f",
     "the names need a relative import"),
    ("an import sharing its line", "A = 1\n", "x = 1; from test_src import A", "shares its line"),
    ("compound-bound", "try:\n    import json\nexcept ImportError:\n    json = None\n\n\n"
                       "def f():\n    return json\n", "from test_src import f",
     "f reads json, which line 5 binds"),
    ("reads its own names", "A = 1\n\n\ndef g():\n    return globals()\n",
     "from test_src import A", "reads its own module's names by name"),
    ("a module import", "A = 1\n", "import test_src", "imports the module"),
    ("a star import", "A = 1\n", "from test_src import *", "star-imports it"),
    ("a dotted import", "A = 1\n", "from tests.test_src import A", "imports it as tests.test_src"),
    ("a patch target", "A = 1\n", 'from test_src import A\nT = "test_src.A"',
     "names test_src.A in a string, and A moves"),
    ("a path", "A = 1\n", 'from test_src import A\nP = "tests/test_src.py"',
     "names its file in a string"),
    ("no importer", "A = 1\n", "B = 2", "no test module imports from it"),
    # TJ-3847
    ("a function's alias of sys", "A = 1\n\n\ndef g():\n    import sys as _s\n    return _s.modules[__name__]\n",
     "from test_src import A", "reads its own module's names by name (sys.modules[__name__])"),
    ("sys.modules by a __module__", "A = 1\n\n\ndef g(self):\n    return sys.modules[type(self).__module__]\n",
     "from test_src import A", "reads its own module's names by name (sys.modules[__name__])"),
    ("import_module of its name", "import importlib\n\nA = 1\n\n\ndef g():\n"
     "    return importlib.import_module(__name__)\n",
     "from test_src import A", "reads its own module's names by name (import_module(__name__))"),
]


class TestRefusals(unittest.TestCase):
    def test_each_is_refused_before_anything_is_written(self):
        for what, code, user, why in REFUSALS:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, {"tests/test_src.py": HEADER + code, "tests/test_user.py": user + "\n"})
                with self.assertRaises(ES.Refused) as cm:
                    _run(r)
                self.assertIn(why, str(cm.exception))
                self.assertFalse((r / "tests/_support").exists())
                self.assertEqual((r / "tests/test_src.py").read_text(), HEADER + code)

    def test_what_is_no_refusal(self):
        # TJ-3818, TJ-3819, TJ-3820, TJ-3825: each of these plans.
        cases = [
            ("a bare name in a list", "A = 1\n", "from test_src import A\nN = ('test_src.py', 'x')\n"),
            ("a bare name compared", "A = 1\n", "from test_src import A\nK = 'x' in ('test_src.py',)\n"),
            ("inspect.signature", "import inspect\n\n\ndef f():\n    return inspect.signature(f)\n",
             "from test_src import f\n"),
            ("vars of an object", "def f(o):\n    return vars(type(o))\n", "from test_src import f\n"),
            ("another module's entry", "A = 1\n\n\ndef g():\n    sys.modules['yfinance_x'] = None\n",
             "from test_src import A\n"),
            ("a string with a space", "A = 1\n", "from test_src import A\nS = 'test_src.A is here'\n"),
            ("an attribute named stack", "def f(o):\n    return o.stack\n", "from test_src import f\n"),
            ("__name__ but no target", "A = 1\nLOG = __name__\n", "from test_src import A\n"),
            ("a dotted string, no __name__", "A = 1\nS = 'x.A'\n", "from test_src import A\n"),
            ("__name__ in the main guard only", "A = 1\nS = 'x.A'\n\nif __name__ == '__main__':\n"
                                                "    unittest.main()\n", "from test_src import A\n"),
            ("a tuple of constants passed", "ROWS = ('a', (1, 2))\nK = os.fspath(ROWS)\n",
             "from test_src import ROWS\n"),
            ("an importer naming its own module", "A = 1\n",
             "import importlib\nfrom unittest import mock\nfrom test_src import A\n\n\ndef g():\n"
             "    return mock.patch.object(importlib.import_module(__name__), 'A', 2)\n"),
            # TJ-3946: a fixture base or a mixin runs no tests where it is bound.
            ("a fixture base by its base's name", "class _Tmp(unittest.TestCase):\n    def _one(self):\n"
                                                  "        return 1\n", "from test_src import _Tmp\n"),
            ("a fixture base by setUp", "class _Fix:\n    def setUp(self):\n        self.x = 1\n",
             "from test_src import _Fix\n"),
            ("a fixture base by setUpClass", "class _Run:\n    @classmethod\n    def setUpClass(cls):\n"
                                             "        cls.x = 1\n", "from test_src import _Run\n"),
            ("a fixture base's fixture base", "class _A(unittest.TestCase):\n    def setUp(self):\n        pass\n\n\n"
                                              "class _B(_A):\n    @classmethod\n    def setUpClass(cls):\n"
                                              "        pass\n", "from test_src import _B\n"),
            ("a mixin of tests", "class Cases:\n    def test_a(self):\n        pass\n", "from test_src import Cases\n"),
            ("a mixin of tests on object", "class Cases(object):\n    def test_a(self):\n        pass\n",
             "from test_src import Cases\n"),
            ("a fixture base on a base from elsewhere", "from _support.x import _Base\n\n\nclass _Mine(_Base):\n"
                                                        "    def setUp(self):\n        pass\n",
             "from test_src import _Mine\n"),
        ]
        for why, code, user in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, {"tests/test_src.py": HEADER + code, "tests/test_user.py": user})
                self.assertEqual(_run(r)[0], 0, why)

    def test_a_string_naming_what_stays_is_left(self):
        # TJ-3796: a string naming the module, or a name that stays, is no
        # reason to refuse it; the extraction leaves it as it was.
        user = 'from test_src import _row\nIDS = ["test_src", "test_src.TestSrc.test_row"]\n'
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_user.py": user})
            self.assertEqual(_run(r)[0], 0)
            self.assertIn('IDS = ["test_src", "test_src.TestSrc.test_row"]',
                          (r / "tests/test_user.py").read_text())

    def test_an_if_or_try_that_only_imports_runs_no_code(self):
        # Only a guard around an import or an assignment: no code an
        # importer would miss (TJ-3790).
        code = ("if os.sep:\n    import json\nelse:\n    json = None\n"
                "try:\n    import shutil\nexcept ImportError:\n    pass\n\n\nA = 1\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": HEADER + code, "tests/test_user.py": "from test_src import A\n"})
            self.assertEqual(_run(r)[0], 0)

    def test_what_stays_may_read_a_moved_name_at_import(self):
        # Reading a moved name, or passing it to a builtin or to a def that
        # changes nothing, is the same in either place (TJ-3790).
        code = ("ROWS = [1]\nACCT, KIND = 'U1234567', ('a', 1)\n\n\ndef _norm(r):\n    return list(r)\n\n\n"
                "N = len(ROWS)\nM = _norm(ROWS)\nK = ROWS.copy()\nJ = os.path.join(ACCT, KIND[0])\n\n\n"
                "class Kept:\n    R = ROWS\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/test_src.py": HEADER + code,
                      "tests/test_user.py": "from test_src import ACCT, KIND, ROWS\n"})
            self.assertEqual(_run(r)[0], 0)
            self.assertIn("M = _norm(ROWS)", (r / "tests/test_src.py").read_text())

    def test_a_missing_source_and_two_that_need_each_other(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            with self.assertRaisesRegex(ES.Refused, "no tests/test_none.py"):
                _run(r, [("test_none", "none", "x")])
        files = {"tests/test_a.py": '"""A."""\nfrom test_b import B\n\n\ndef fa():\n    return B\n',
                 "tests/test_b.py": '"""B."""\nfrom test_a import fa\n\n\ndef B():\n    return fa\n',
                 "tests/test_user.py": "from test_a import fa\nfrom test_b import B\n"}
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, files)
            with self.assertRaisesRegex(ES.Refused, "each source left needs another's names"):
                _run(r, [("test_a", "a", "x"), ("test_b", "b", "y")])

    def test_a_support_module_that_exists_and_a_bad_table(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, {"tests/_support/sup.py": "x = 1\n"})
            with self.assertRaisesRegex(ES.Refused, "sup.py exists"):
                _run(r)
        for bad in ("test_src sup", "src sup the rows", "test_src Sup the rows",
                    "test_src test_sup the rows", "test_src a x\ntest_src b y",
                    "test_a sup x\ntest_b sup y"):
            with self.subTest(bad), self.assertRaises(ES.Refused):
                ES.read_table(bad)
        self.assertEqual(ES.read_table("# a comment\ntest_src sup The rows.\n"),
                         [("test_src", "sup", "The rows")])
        # TJ-3801: the error names what is wrong.
        with self.assertRaisesRegex(ES.Refused, "line 1: the source src_x is no test module"):
            ES.read_table("src_x sup the rows")
        with self.assertRaisesRegex(ES.Refused, "line 2: the support module Sup: lower case"):
            ES.read_table("test_a a x\ntest_src Sup the rows")


class TestScopes(unittest.TestCase):
    def reads(self, code):
        return ES.module_reads(ast.parse(code).body[0])

    def test_a_functions_own_names_are_not_the_modules(self):
        self.assertEqual(self.reads("def f(x, y=D):\n    H = 1\n    return H + x + G\n"),
                         {"D", "G"})
        self.assertEqual(self.reads("def f():\n    global G\n    G = 1\n    return G\n"), {"G"})
        self.assertEqual(self.reads("def f():\n    def g():\n        return a\n    a = 1\n"
                                    "    return g\n"), set())

    def test_classes_comprehensions_and_lambdas(self):
        self.assertEqual(self.reads("class C(B):\n    X = Y\n    def m(self):\n"
                                    "        return X\n"), {"B", "Y", "X"})
        self.assertEqual(self.reads("A = [a for a in L if a > T]\n"), {"L", "T"})
        self.assertEqual(self.reads("A = lambda v, w=W: v + U\n"), {"W", "U"})
        self.assertEqual(ES.module_binds(ast.parse("A = [(b := a) for a in L]\n").body[0]),
                         {"A", "b"})


# BASE's check_architecture, as a stand-in: it records each tests/ file's
# count of `_row(` as E402 hits, which move with the code (TJ-3822).
STAND_IN = (
    '"""A stand-in for check_architecture."""\n'
    'import json\nimport sys\nfrom pathlib import Path\n\n'
    'ROOT = Path(__file__).resolve().parent.parent\n'
    'B = ROOT / "scripts" / "architecture-baseline.json"\n'
    'if "--accept-growth=ruff,noqa" in sys.argv:\n'
    '    doc = json.loads(B.read_text())\n'
    '    counts = {f.relative_to(ROOT).as_posix(): f.read_text().count("_row(")\n'
    '              for f in sorted((ROOT / "tests").rglob("*.py")) if "_row(" in f.read_text()}\n'
    '    doc["ruff"] = {"E402": counts}\n'
    '    B.write_text(json.dumps(doc) + "\\n")\n')


def _check_repo(r: Path, edit=None, baseline=None, files=None, base_edit=None):
    """BASE (the synthetic repository with the tools, FILES too, then
    BASE_EDIT) and HEAD (the extraction, its baseline recorded, or
    BASELINE(that record), then EDIT), as staged trees."""
    base_doc = {"ruff": {"E402": {"tests/test_src.py": 2, "tests/x.py": 1}}, "noqa": {},
                "function_length": {}}
    _repo(r, {"scripts/architecture-baseline.json": json.dumps(base_doc) + "\n",
              "scripts/check_architecture.py": STAND_IN,
              "docs/notes.md": "notes\n", "tests/notes.md": "notes\n", "docs/notes.txt": "x\n",
              **(files or {})}, tools=True)
    if base_edit:
        base_edit(r)
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    base = _git(r, "write-tree").strip()
    _run(r)
    if (r / "scripts/check_architecture.py").is_file():      # (a failing one: as it is)
        subprocess.run([sys.executable, "-B", "scripts/check_architecture.py",
                        "--accept-growth=ruff,noqa"], cwd=r, capture_output=True)
    if baseline:
        b = r / "scripts/architecture-baseline.json"
        b.write_text(json.dumps(baseline(json.loads(b.read_text()))) + "\n")
    if edit:
        edit(r)
    _git(r, "add", "-A")
    return base, _git(r, "write-tree").strip()


def _check(r, base, head, allow=()):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        rc = ES.check(r, base, head, list(allow), True)
    return rc, out.getvalue()


def _append(rel, text):
    def edit(r):
        with open(r / rel, "a", encoding="utf-8") as f:
            f.write(text)
    return edit


# A fixture base with a class-level marker (as tests/tax_rules' `@rule` sets
# it) and skip, a mixin with a marked test, and their subclasses (TJ-3946).
RULE = "def _rule(c):\n    c.__tax_rules__ = [('rule', 'CA-X-01', 'canada')]\n    return c\n\n\n"
BASE_CLASSES = ("@_rule\n" "@unittest.skipIf(os.sep, 'tj skip')\nclass _Base(unittest.TestCase):\n"
                "    def _one(self):\n        return 1\n\n\n"
                "class Cases:\n    @_rule\n    def test_m(self):\n        pass\n")
FIXTURE_SRC = (HEADER + "\n\n" + RULE + BASE_CLASSES + "\n\nclass TestSrc(Cases, _Base):\n"
               "    def test_a(self):\n        self.assertEqual(self._one(), 1)\n")
FIXTURE_USER = ("import unittest\n\nfrom test_src import Cases, _Base\n\n\n"
                "class TestUser(Cases, _Base):\n    def test_b(self):\n        pass\n\n\n"
                "class TestMixed(Cases, unittest.TestCase):\n    pass\n")


def _ids_trees(r: Path, support: str, classes: str = BASE_CLASSES):
    """BASE (FIXTURE_SRC with CLASSES, FIXTURE_USER) and a HEAD whose
    tests/_support/sup.py is SUPPORT, written by hand, as directories."""
    trees = {"base": {"tests/test_src.py": FIXTURE_SRC.replace(BASE_CLASSES, classes),
                      "tests/test_user.py": FIXTURE_USER},
             "head": {"tests/_support/__init__.py": "", "tests/_support/sup.py": support,
                      "tests/test_src.py": FIXTURE_SRC.replace(RULE + BASE_CLASSES,
                                                               "from _support.sup import Cases, _Base\n"),
                      "tests/test_user.py": FIXTURE_USER.replace("from test_src", "from _support.sup")}}
    for name, files in trees.items():
        for rel, text in files.items():
            (r / name / rel).parent.mkdir(parents=True, exist_ok=True)
            (r / name / rel).write_text(text, encoding="utf-8")
    (r / "home").mkdir()
    return r / "base", r / "head"


class TestTheCheck(unittest.TestCase):
    def test_the_tools_run_passes(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r)
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("check: 1 source(s): test_src -> sup", out)
            self.assertIn("the baseline as BASE's check_architecture records it; beyond it, by "
                          "hand: none", out)
            self.assertIn("check: state: 1 source(s)", out)
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(
                r, base_edit=lambda r: (r / "scripts/check_architecture.py").unlink())
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("no check_architecture in BASE to record a baseline", out)
            # The documented route: BASE's copy, isolated.
            run = subprocess.run([sys.executable, "-I", "-S", str(r / "scripts/extract_support.py"),
                                  "--check", base, head, "--trees", "--no-ids", f"--repo={r}"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("HEAD is BASE's tool run on BASE", run.stdout)

    def test_what_fails(self):
        marker = "tests/ran.txt"
        evil = (f'"""Shared test helpers: x.\n\nShared from: test_none\n"""\n'
                f'open({marker!r}, "w").write("ran")\n')
        cases = [
            ("an importer's code", _append("tests/test_user.py", "X = 1\n"), None,
             "tests/test_user.py is not what BASE's tool writes"),
            ("the support module", _append("tests/_support/sup.py", "# more\n"), None,
             "tests/_support/sup.py is not what"),
            ("the tool", _append("scripts/extract_support.py", "# more\n"), None,
             "scripts/extract_support.py is not what"),
            ("a doc not allowed", _append("docs/notes.md", "more\n"), None,
             "docs/notes.md is not what"),
            ("a header not the tool's",
             lambda r: (r / "tests/_support/sup.py").write_text('"""Helpers."""\n'), None,
             "not the tool's"),
            ("a source BASE lacks",
             lambda r: (r / "tests/_support/evil.py").write_text(evil), None,
             "BASE's tool refused HEAD's table"),
            # TJ-3822: the baseline is BASE's check_architecture's record.
            ("a count moved to another file", None, lambda d: {**d, "ruff": {"E402": {
                "tests/test_test_layout.py": sum(d["ruff"]["E402"].values())}}}, "scripts/architecture-baseline.json is not what BASE's tool writes"),
            ("another key", None, lambda d: {**d, "function_length": {"a": [1]}}, "scripts/architecture-baseline.json is not what BASE's tool writes"),
            ("equal to Python", None, lambda d: {**d, "ruff": {"E402": {
                k: float(c) for k, c in d["ruff"]["E402"].items()}}}, "scripts/architecture-baseline.json is not what BASE's tool writes"),
            ("a count that is none", None, lambda d: {**d, "ruff": {"E402": {
                k: c - 0.5 if n else c + 0.5
                for n, (k, c) in enumerate(d["ruff"]["E402"].items())}}}, "scripts/architecture-baseline.json is not what BASE's tool writes"),
            ("the baseline's mode",
             lambda r: os.chmod(r / "scripts/architecture-baseline.json", 0o755), None, "scripts/architecture-baseline.json is not what BASE's tool writes"),
            ("the baseline's spacing", lambda r: (r / "scripts/architecture-baseline.json").write_text(
                (r / "scripts/architecture-baseline.json").read_text().replace(": ", ":  ")), None,
             "scripts/architecture-baseline.json is not what BASE's tool writes"),
            # TJ-3788: what `git archive` hides is compared too.
            ("a file .gitattributes hides", lambda r: (
                (r / ".gitattributes").write_text(".gitattributes export-ignore\n"
                                                  "tests/test_0_planted.py export-ignore\n"),
                (r / "tests/test_0_planted.py").write_text("import unittest\n")), None,
             "tests/test_0_planted.py is not what"),
            # TJ-3789: a .pyc, which Python would run for its module.
            ("a .pyc", lambda r: ((r / "tests/__pycache__").mkdir(exist_ok=True),
                                  (r / "tests/__pycache__/test_user.cpython-312.pyc")
                                  .write_bytes(b"\x00pyc")), None,
             "tests/__pycache__/test_user.cpython-312.pyc is not what"),
            ("a mode", lambda r: os.chmod(r / "tests/test_user.py", 0o755), None,
             "tests/test_user.py is not what"),
            ("a symlink", lambda r: (r / "tests/link.py").symlink_to("test_user.py"), None,
             "tests/link.py: a symlink or a submodule"),
        ]
        for what, edit, baseline, why in cases:
            with self.subTest(what), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, edit, baseline)
                rc, out = _check(r, base, head)
                self.assertEqual(rc, 1, out)
                self.assertIn(why, out)
                self.assertIn("nothing of HEAD's was run", out)
        # A failed batch's code never runs, the ids check included.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, lambda r: (r / "tests/_support/evil.py").write_text(evil))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertEqual(ES.check(r, base, head, [], False), 1)
            self.assertFalse(any(Path(td).rglob("ran.txt")))

    def test_the_state_check(self):
        # TJ-3816, TJ-3817, TJ-3829, TJ-3830: what an importer of the moved
        # names, run alone, and the source itself see after the move is
        # what they saw before; the static refusals pass each of these.
        mod = "state: an importer of test_src's moved names"
        cases = [
            ("a decorator through an alias",
             "CASES = []\n_add = CASES.append\n\n\ndef case(f):\n    _add(f)\n    return f\n\n\n"
             "@case\ndef _kept():\n    return 1\n",
             "from test_src import CASES, case\n", {}, f"{mod}: CASES holds something else"),
            ("setattr on a moved class", "class Cfg:\n    pass\n\n\nK = setattr(Cfg, 'x', 1)\n",
             "from test_src import Cfg\n", {}, f"{mod}: Cfg holds something else"),
            ("a dunder on a moved class", "class Row:\n    pass\n\n\n_R = Row\n\n\ndef _eq(f):\n"
             "    setattr(_R, '__eq__', f)\n    return f\n\n\n" "@_eq\ndef _same(a, b):\n    return True\n",
             "from test_src import Row\n", {}, f"{mod}: Row holds something else"),
            ("os.putenv in code that stays", "A = 1\nK = os.putenv('TJ_X_STATE', '1')\n",
             "from test_src import A\n", {}, f"{mod}: the environment differs: TJ_X_STATE"),
            ("sys.path in code that stays", "A = 1\nK = list.insert(sys.path, 0, '/tj_state_x')\n",
             "from test_src import A\n", {}, f"{mod}: sys.path differs: entry /tj_state_x"),
            ("a side effect that moves", "HOME_X = os.environ.setdefault('TJ_Y_STATE', '1')\n",
             "from test_src import HOME_X\n", {}, "state: test_src: the environment differs: TJ_Y_STATE"),
            ("a counter drawn", "import itertools\n\n_SEQ = itertools.count(1)\n\n\ndef next_id():\n"
             "    return next(_SEQ)\n\n\nFIRST = next_id()\n", "from test_src import next_id\n", {},
             f"{mod}: _SEQ holds something else"),
            ("a random draw", "import random\n\nRNG = random.Random(7)\n\n\ndef draw():\n"
             "    return RNG.random()\n\n\nSAMPLE = draw()\n", "from test_src import draw\n", {},
             f"{mod}: RNG holds something else"),
            ("the global random state", "import random\n\nA = 1\nK = random.random()\n",
             "from test_src import A\n", {}, f"{mod}: the random state differs"),
            ("a deque filled", "import collections\n\nQ = collections.deque()\n_K = Q.appendleft(1)\n",
             "from test_src import Q\n", {}, f"{mod}: Q holds something else"),
            ("a moved module object", "import types\n\nFAKE = types.ModuleType('fake_x')\n\n\n"
             "def _fake(f):\n    setattr(FAKE, f.__name__, f)\n    return f\n\n\n" "@_fake\n"
             "def download(*a):\n    return 'rows'\n", "from test_src import FAKE\n", {},
             f"{mod}: FAKE holds something else"),
            ("a fake in sys.modules", "import _fakes\n\nA = 1\n_YF = _fakes.install()\n",
             "from test_src import A\n",
             {"tests/_fakes.py": "import sys\nimport types\n\n\ndef install():\n"
                                 "    m = types.ModuleType('yfinance_x')\n    sys.modules['yfinance_x'] = m\n"
                                 "    return m\n"},
             f"{mod}: fake modules differ: yfinance_x"),
            ("the decimal context", "import decimal\n\ndecimal.getcontext().prec = 6\n\n\ndef third():\n"
             "    return decimal.Decimal(1) / 3\n", "from test_src import third\n", {},
             f"{mod}: the decimal context differs"),
            ("deeper than the walk", "BOX = " + "[" * 16 + "]" * 16 + "\n", "from test_src import BOX\n",
             {}, f"{mod}: too deep to compare (DEPTH, BUDGET in _SNAP), or holding what the walk sees by its type alone: BOX"),
            ("another module changed", "import _reg\n\nA = 1\nK = list.extend(_reg.ROWS, [A])\n",
             "import _reg\nfrom test_src import A\n", {"tests/_reg.py": "ROWS = []\n"},
             f"{mod}: _reg's ROWS differ"),
            ("a path a memo's name holds", "import _reg\n\nA = 1\nK = setattr(_reg, 'CACHE_FILE', 'b')\n",
             "import _reg\nfrom test_src import A\n", {"tests/_reg.py": "CACHE_FILE = 'a'\n"},
             f"{mod}: _reg's CACHE_FILE differ"),
            # An import only the source had: for its side effect, or one that
            # fills another module (of tests/ or src/).
            ("an import for its effect", "import _envset  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n", {"tests/_envset.py": "import os\nos.environ['TJ_Z_STATE'] = '1'\n"},
             f"{mod}: it no longer imports _envset, and importing _envset changes TJ_Z_STATE"),
            ("an import that fills a registry", "import _cases  # noqa: F401\n\n\ndef parse(s):\n"
             "    return int(s)\n", "from test_src import parse\n",
             {"tests/_reg.py": "CASES = []\n", "tests/_cases.py": "import _reg\n\n_reg.CASES.append(1)\n"},
             f"{mod}: it no longer imports _cases, and importing _cases changes _reg.CASES"),
            ("a chain of imports", "import _h1  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_h1.py": "import _h2  # noqa: F401\n",
              "tests/_h2.py": "import os\nos.environ['TJ_H2_STATE'] = '1'\n"},
             f"{mod}: it no longer imports _h2, and importing _h2 changes TJ_H2_STATE"),
            ("an import that sets the decimal context", "import _decset  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n", {"tests/_decset.py": "import decimal\n\ndecimal.getcontext().prec = 5\n"},
             f"{mod}: it no longer imports _decset, and importing _decset changes decimal"),
            ("an import that installs a fake", "import _fake2  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_fake2.py": "import sys\nimport types\n\nsys.modules['yfinance_y'] = types.ModuleType('y')\n"},
             f"{mod}: it no longer imports _fake2, and importing _fake2 changes sys.modules['yfinance_y']"),
            ("an entry moved to the front", "A = 1\nK = list.insert(sys.path, 0, os.path.join(os.path.dirname("
             "os.path.abspath(__file__)), '..', 'src'))\n", "from test_src import A\n",
             {"tests/_shadow_x.py": "X = 1\n", "src/_shadow_x.py": "X = 2\n"},
             f"{mod}: sys.path differs: _shadow_x from <ROOT>/src or <ROOT>/tests"),
            ("another module too deep", "import _deep  # noqa: F401\n\nA = 1\n",
             "import _deep  # noqa: F401\nfrom test_src import A\n",
             {"tests/_deep.py": "BIG = " + "[" * 16 + "]" * 16 + "\n"},
             f"{mod}: too deep to compare (DEPTH, BUDGET in _SNAP), or holding what the walk sees by its type alone: _deep.BIG"),
            ("a package that registers its plugin", "import _cases  # noqa: F401\n\nA = 1\n",
             "import _reg\nfrom test_src import A\n",
             {"tests/_reg.py": "CASES = []\n", "tests/_cases/__init__.py": "from _cases import ib  # noqa: F401\n",
              "tests/_cases/ib.py": "import _reg\n\n_reg.CASES.append(1)\n"}, "changes _reg.CASES"),
            ("a src package that registers its plugin", "import plug  # noqa: F401\n\nA = 1\n",
             "import plug_core\nfrom test_src import A\n",
             {"src/plug_core.py": "REG = {}\n\n\ndef register(k):\n    REG[k] = 1\n",
              "src/plug/__init__.py": "from plug import ib  # noqa: F401\n",
              "src/plug/ib.py": "from plug_core import register\n\nregister('ib')\n"}, "changes plug_core.REG"),
            ("two helpers importing each other", "import _cases  # noqa: F401\n\nA = 1\n",
             "import _reg\nfrom test_src import A\n",
             {"tests/_reg.py": "CASES = []\n",
              "tests/_cases.py": "import _reg\nimport _more  # noqa: F401\n\n_reg.CASES.append(1)\n",
              "tests/_more.py": "import _cases  # noqa: F401\n\nKIND = 'x'\n"}, "changes _reg.CASES"),
            ("a nested class filled by what stays",
             "class Reg:\n    class Rows:\n        items = []\n\n\n_add = Reg.Rows.items.append\n\n\n"
             "def case(f):\n    _add(f)\n    return f\n\n\n" "@case\ndef _kept():\n    return 1\n",
             "from test_src import Reg\n", {}, f"{mod}: Reg holds something else"),
            ("a generator advanced by what stays",
             "def _gen():\n    n = 0\n    while True:\n        n += 1\n        yield n\n\n\nIDS = _gen()\n\n\n"
             "def next_id():\n    return next(IDS)\n\n\nFIRST = next_id()\n",
             "from test_src import next_id\n", {}, f"{mod}: IDS holds something else"),
            ("an import that needs the source loaded", "A = 1\nimport _gated  # noqa: F401,E402\n",
             "from test_src import A\n",
             {"tests/_gated.py": "import sys\n\nif 'test_src' not in sys.modules:\n    raise RuntimeError('gated')\n"},
             f"{mod}: it no longer imports _gated, and its import fails there (RuntimeError)"),
            # TJ-3865 to TJ-3870
            ("a generator delegating to another", "import itertools\n\n\ndef _gen():\n"
             "    yield from itertools.count(1)\n\n\nIDS = _gen()\n\n\ndef next_id():\n    return next(IDS)\n\n\n"
             "FIRST = next_id()\n", "from test_src import next_id\n", {}, f"{mod}: IDS holds something else"),
            ("an object with an outside base", "import random\n\n\nclass _Rng(random.Random):\n    pass\n\n\n"
             "RNG = _Rng(5)\n\n\ndef draw():\n    return RNG.random()\n\n\nFIRST = draw()\n",
             "from test_src import draw\n", {}, f"{mod}: RNG holds something else"),
            ("a stub planted with a spec", "import _plant  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_plant.py": "import importlib.machinery\nimport importlib.util\nimport sys\n\n"
                                 "sys.modules['stub_yf'] = importlib.util.module_from_spec("
                                 "importlib.machinery.ModuleSpec('stub_yf', None))\n"},
             "importing _plant changes sys.modules['stub_yf']"),
            ("a module planted under another's name", "import _plant  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_plant.py": "import sys\n\nimport _fake_cs\n\nsys.modules['colorsys'] = _fake_cs\n",
              "tests/_fake_cs.py": "X = 1\n"}, "importing _plant changes sys.modules['colorsys']"),
            # TJ-3912: a module of the tree planted below an outside package
            # is a plant (an outside one there is that package's: os.path).
            ("a module of the tree planted below an outside package", "import _plant  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_plant.py": "import json  # noqa: F401\nimport sys\n\nimport _fake_cs\n\n"
                                 "sys.modules['json.tj_tree'] = _fake_cs\n",
              "tests/_fake_cs.py": "X = 1\n"}, "importing _plant changes sys.modules['json.tj_tree']"),
            # TJ-3869: a lost module whose state moves with HOME, which an
            # importer's test may now import first: a product module, or a
            # test helper an importer imports in a function, names in a
            # string or reaches through a helper it so imports.
            ("a lost product module that reads HOME", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads plug.cfg, whose state moves with the environment (WHERE)"),
            ("a lost helper an importer imports in a test", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a lost copy of the environment a patch names", "import _envcopy  # noqa: F401\n\nA = 1\n",
             "from unittest import mock\n\nfrom test_src import A\n\nP = mock.patch('_envcopy.ENV', {})\n",
             {"tests/_envcopy.py": "import os\n\nENV = os.environ.copy()\n"},
             f"{mod}: tests/test_user.py no longer loads _envcopy, whose state moves with the environment (ENV)"),
            ("a lost helper reached through another", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _mid\n    return _mid\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n",
              "tests/_mid.py": "import _cfg  # noqa: F401\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            # TJ-3904, TJ-3933: every lost module is replayed under a moved
            # environment: a file below HOME read, HOME read through a bound
            # environ, a variable below HOME, one whose replay fails.
            ("a lost helper that keeps a file below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": READ_HOME_FILE},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plugcfg)"),
            ("a lost helper that looks for a file below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.path.exists(os.path.expanduser('~/.plugcfg'))\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plugcfg)"),
            ("a lost helper that looks at HOME itself", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.path.isdir(os.path.expanduser('~'))\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~)"),
            # TJ-3936: the source is a lost module too, its kept state replayed.
            ("a source whose kept state moves", "WHERE = os.path.expanduser('~/x')\nA = 1\n",
             "from unittest import mock\n\nfrom test_src import A\n\nP = mock.patch('test_src.WHERE', '/x')\n", {},
             f"{mod}: tests/test_user.py no longer loads test_src, whose state moves with the environment (WHERE)"),
            ("a lost helper that lists a folder below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\ntry:\n    NAMES = os.listdir(os.path.expanduser('~/.plug'))\n"
                               "except OSError:\n    NAMES = []\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plug)"),
            ("a lost helper that scans a folder below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\ntry:\n    NAMES = [e.name for e in os.scandir(os.path.expanduser('~/.plug'))]\n"
                               "except OSError:\n    NAMES = []\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plug)"),
            ("HOME read through a bound environ", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "", "src/plug/paths.py": "from os import environ\n\n\ndef home():\n"
                                                               "    return environ['HOME']\n",
              "src/plug/cfg.py": "from plug.paths import home\n\nWHERE = home() + '/x'\n"},
             f"{mod}: tests/test_user.py no longer loads plug.cfg, whose state moves with the environment (WHERE)"),
            # (set in a loop: no test names it, so it moves as one below HOME)
            ("a variable below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.environ['XDG_CACHE_HOME']\n",
              "tests/_hermetic/__init__.py": "import os\nimport tempfile\n\nos.environ['HOME'] = tempfile.mkdtemp()\n"
                                             "for k in ('XDG_CACHE_HOME',):\n"
                                             "    os.environ[k] = os.path.join(os.environ['HOME'], '.cache')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a lost module whose replay fails", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "import os\nfrom unittest import mock\n\nfrom test_src import A\n\n"
             "P = mock.patch.dict(os.environ, {'TJ_FAIL_STATE': ''})\n",
             {"src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nif os.environ.get('TJ_FAIL_STATE'):\n"
                                                             "    raise RuntimeError('moved')\n"},
             f"{mod}: tests/test_user.py no longer loads plug.cfg, whose state may move with the environment "
             f"(its replay fails, so the check cannot tell): a test"),
            ("the source's own import no longer loads it", "import _cfg\n\n\ndef A():\n    return _cfg.WHERE\n",
             "from test_src import A\n", {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             "state: test_src: its import no longer loads _cfg, whose state moves with the environment (WHERE)"),
            # TJ-3905: an importer through a helper, a package's test (its
            # bootstrap puts tests/ on the path, as taxjson-fetch's does), one
            # whose import fails (counted).
            ("an importer behind a helper", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _h  # noqa: F401\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_h.py": "from test_src import A  # noqa: F401\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a package's test", "from plug import cfg  # noqa: F401\n\nA = 1\n", None,
             {"packages/p/tests/_bootstrap.py": BOOTSTRAP,
              "packages/p/tests/test_pu.py": "import _bootstrap  # noqa: F401\nfrom test_src import A  # noqa: F401\n",
              "src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: packages/p/tests/test_pu.py no longer loads plug.cfg, whose state moves with the environment (WHERE)"),
            ("an importer that needs the setup first", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _onpath  # noqa: F401\nfrom test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_hermetic/__init__.py": "import os\nimport sys\n\nsys.path.append(os.path.join(os.path.dirname("
                                             "os.path.dirname(os.path.abspath(__file__))), 'more'))\n",
              "tests/more/_onpath.py": "", "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("an importer whose import fails", "from plug import cfg  # noqa: F401\n\nA = 1\n", None,
             {"packages/p/tests/test_pu.py": "from test_src import A  # noqa: F401\n",
              "src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: packages/p/tests/test_pu.py (its import fails on BASE and HEAD) no longer loads plug.cfg"),
            # TJ-3933 to TJ-3937: a package whose __init__ reads HOME and
            # imports its submodule first; HOME memoized in a module that
            # stays; os.access and os.path.lexists below HOME; a helper that
            # imports the source in a function it calls at import, or by
            # import_module; an earlier batch's support module; a variable
            # a test sets, outside HOME.
            ("a package that reads HOME after its submodule", "import plug  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "from plug import sub  # noqa: F401\nimport os\n\nWHERE = os.path.expanduser('~/x')\n",
              "src/plug/sub.py": "X = 1\n"},
             f"{mod}: tests/test_user.py no longer loads plug, whose state moves with the environment (WHERE)"),
            ("HOME memoized in a module that stays", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "", "src/plug/paths.py": "import functools\nimport os\n\n\n"
              "@functools.lru_cache(maxsize=None)\ndef home():\n    return os.environ['HOME']\n",
              "src/plug/cfg.py": "from plug.paths import home\n\nWHERE = home() + '/x'\n"},
             f"{mod}: tests/test_user.py no longer loads plug.cfg, whose state moves with the environment "
             f"(WHERE, home)"),
            ("os.access below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.access(os.path.expanduser('~/.plugcfg'), os.R_OK)\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plugcfg)"),
            ("os.path.lexists below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.path.lexists(os.path.expanduser('~/.plugcfg'))\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.plugcfg)"),
            ("an importer through a call at import", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _h  # noqa: F401\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_h.py": "def _get():\n    from test_src import A\n    return A\n\n\nA = _get()\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("an importer through import_module", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import importlib\n\nU = importlib.import_module('test_u2')\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/test_u2.py": "from test_src import A  # noqa: F401\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("an earlier batch's support module", "from _support.a import WHERE\n\nA = 1\n\n\ndef kept():\n    return WHERE\n",
             "from test_src import A\n\n\ndef later():\n    import _support.a\n    return _support.a\n",
             {"tests/_support/__init__.py": '"""What test modules share."""\n',
              "tests/_support/a.py": '"""Shared test helpers: a.\n\nShared from: test_a\n"""\nimport os\n\n'
                                     "WHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _support.a, whose state moves with the environment (WHERE)"),
            ("an earlier batch's support module with an effect",
             "from _support.a import WHERE\n\nA = 1\n\n\ndef kept():\n    return WHERE\n", "from test_src import A\n",
             {"tests/_support/__init__.py": '"""What test modules share."""\n',
              "tests/_support/a.py": '"""Shared test helpers: a.\n\nShared from: test_a\n"""\nimport os\n\n'
                                     "WHERE = 1\n_K = os.environ.setdefault('TJ_SUPA_STATE', '1')\n"},
             f"{mod}: it no longer imports _support.a, and importing _support.a changes TJ_SUPA_STATE"),
            ("a variable a test sets", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\nfrom unittest import mock\n\nfrom test_src import A\n\n\ndef later():\n"
             "    with mock.patch.dict(os.environ, TAXJSON_LOCAL_TZ='Asia/Tokyo'):\n        import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nTZ = os.environ.get('TAXJSON_LOCAL_TZ', 'UTC')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (TZ)"),
            ("a variable set by a store", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    os.environ['TJ_STORE_STATE'] = 'x'\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nV = os.environ.get('TJ_STORE_STATE')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (V)"),
            ("a variable a path names", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later(mp):\n    mp.setenv('TJ_PATHVAR_STATE', '/nonexistent/x')\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.path.isfile(os.path.join(os.environ.get('TJ_PATHVAR_STATE', '/'), 'f'))\n",
              "tests/_hermetic/__init__.py": "import os\n\nos.environ['TJ_PATHVAR_STATE'] = '/nonexistent/y'\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/env/TJ_PATHVAR_STATE/f)"),
            # TJ-3938: the lazy reach through a helper the importer loads; the
            # support module's own lazy import (HEAD's index); a module of a
            # package's src/ is the product's.
            ("a helper's lazy import", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _mk  # noqa: F401\nfrom test_src import A\n",
             {"tests/_mk.py": "def make():\n    import _cfg\n    return _cfg\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a lazy import of a helper's lazy import", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _mk  # noqa: F401\nfrom test_src import A\n",
             {"tests/_mk.py": "def make():\n    import _mk2\n    return _mk2\n",
              "tests/_mk2.py": "def make2():\n    import _cfg\n    return _cfg\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a helper's lazy import in a lambda", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _mk  # noqa: F401\nfrom test_src import A\n",
             {"tests/_mk.py": "import importlib\n\nMAKE = lambda: importlib.import_module('_cfg')\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("the support module's own lazy import", "import _cfg  # noqa: F401\n\n\ndef A():\n    import _cfg\n"
             "    return _cfg.WHERE\n", "from test_src import A\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WHERE)"),
            ("a module of a package's src", "from plugp import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"packages/p/src/plugp/__init__.py": "",
              "packages/p/src/plugp/cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py no longer loads plugp.cfg, whose state moves with the environment (WHERE)"),
            # TJ-3933: a module reference that moves, a module only the moved
            # import loads, a name the walk cuts short.
            ("a module reference that moves", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    os.environ['TJ_M_STATE'] = '1'\n"
             "    import _cfg\n    return _cfg\n",
             {"src/plug/__init__.py": "", "src/plug/a.py": "", "src/plug/b.py": "",
              "tests/_cfg.py": "import os\n\nimport plug.a\nimport plug.b\n\n"
                               "M = plug.a if os.environ.get('TJ_M_STATE') else plug.b\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (M)"),
            ("a module only the moved import loads", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    os.environ['TJ_SUB_STATE'] = '1'\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_extra.py": "X = 1\n",
              "tests/_cfg.py": "import os\n\nif os.environ.get('TJ_SUB_STATE'):\n    __import__('_extra')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (_extra)"),
            ("a name the walk cuts short", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import queue\n\nX = queue.SimpleQueue()\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (X)"),
            # TJ-3906, TJ-3912: an outside base's state in its reduce's
            # arguments; one a reduce does not read cuts the walk.
            ("an object with an outside base, by its arguments", "class _B(bytearray):\n    pass\n\n\n"
             "BUF = _B(b'a')\n\n\ndef grow():\n    return BUF.__iadd__(b'b')\n\n\nFIRST = grow()\n",
             "from test_src import grow\n", {}, f"{mod}: BUF holds something else"),
            ("an object an outside base keeps to itself", "from _qq import Q\n\nBOX = Q()\n\n\n"
             "def push(x):\n    return BOX.put(x)\n\n\ndef first():\n    return BOX.get_nowait()\n\n\nFIRST = push(1)\n",
             "from test_src import first\n",
             {"tests/_qq.py": "import queue\n\n\nclass Q(queue.SimpleQueue):\n    pass\n"},
             f"{mod}: too deep to compare (DEPTH, BUDGET in _SNAP), or holding what the walk sees by its "
             f"type alone: BOX"),
            ("a container named cache filled", "import _reg\n\nA = 1\nK = dict.update(_reg._X_CACHE, a=1)\n",
             "import _reg\nfrom test_src import A\n", {"tests/_reg.py": "_X_CACHE = {}\nROWS = []\n"},
             f"{mod}: _reg's _X_CACHE differ"),
            # TJ-3872: a repository package below a lost import is no outside one.
            ("a lost import of a package's module", "import _lm  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_lm.py": "import pkgx.sub  # noqa: F401\n",
              "tests/pkgx/__init__.py": "import os\n\nos.environ['TJ_PKGX'] = '1'\n", "tests/pkgx/sub.py": ""},
             "TJ_PKGX"),
            ("a lost import of a namespace package's module", "import _lm  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_lm.py": "import nsx.mod  # noqa: F401\n",
              "tests/nsx/mod.py": "import os\n\nos.environ['TJ_NSX'] = '1'\n"},
             "TJ_NSX"),
            ("an import that sets a decimal trap", "import _dectrap  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_dectrap.py": "import decimal\n\ndecimal.getcontext().traps[decimal.Inexact] = True\n"},
             f"{mod}: it no longer imports _dectrap, and importing _dectrap changes decimal"),
            ("a fake below a fake", "import _plant  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_plant.py": "import sys\nimport types\n\nsys.modules['_fakepkg'] = types.ModuleType('_fakepkg')\n"
                                 "sys.modules['_fakepkg.sub'] = 1\n"}, "sys.modules['_fakepkg.sub']"),
            ("a fake below a module of the tree", "import _plant  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_reg.py": "CASES = []\n",
              "tests/_plant.py": "import sys\n\nimport _reg  # noqa: F401\n\nsys.modules['_reg.extra'] = 1\n"},
             "importing _plant changes sys.modules['_reg.extra']"),
            ("a fake module below a package", "import _plant  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_plant.py": "import json  # noqa: F401\nimport sys\nimport types\n\n"
                                 "sys.modules['json.tj_fake2'] = types.ModuleType('json.tj_fake2')\n"},
             "importing _plant changes sys.modules['json.tj_fake2']"),
            ("the source loses an import's effect", "import _fx\n\n\ndef helper():\n    return _fx.X\n",
             "from test_src import helper\n",
             {"tests/_fx.py": "import os\n\nos.environ['TJ_FX_STATE'] = '1'\nX = 1\n"},
             "state: test_src: it no longer imports _fx, and importing _fx changes TJ_FX_STATE"),
            ("a test helper filling a product's memo", "import _fill  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "_MEMO = {}\n", "tests/_fill.py": "import plug\n\nplug._MEMO['t'] = 1\n"},
             f"{mod}: it no longer imports _fill, and importing _fill changes plug._MEMO"),
            ("a product module's private registry", "import plug.ib  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "_REG = {}\n\n\ndef register(k):\n    _REG[k] = 1\n\n\n"
                                      "def names():\n    return sorted(_REG)\n",
              "src/plug/ib.py": "import plug\n\nplug.register('ib')\n"},
             f"{mod}: it no longer imports plug.ib, and importing plug.ib changes plug._REG"),
            ("a plugin of src/", "import plug.ib  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"src/plug/__init__.py": "REG = {}\n", "src/plug/ib.py": "import plug\n\nplug.REG['ib'] = 1\n"},
             f"{mod}: it no longer imports plug.ib, and importing plug.ib changes plug.REG"),
            # TJ-3964: every path an import makes, removes or reads a link of.
            ("a lost module that makes folders below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nos.makedirs(os.path.expanduser('~/.cache/tj'), exist_ok=True)\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment "
             f"(~, ~/.cache, ~/.cache/tj)"),
            ("a lost module that removes a file below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\ntry:\n    os.unlink(os.path.expanduser('~/.tj-stale'))\n"
                               "except OSError:\n    pass\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.tj-stale)"),
            ("a lost module that reads a link below HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\ntry:\n    T = os.readlink(os.path.expanduser('~/.tjlink'))\n"
                               "except OSError:\n    T = None\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (~/.tjlink)"),
            # TJ-3965: a cache wrapper's entries.
            ("a product's lru_cache a lost module fills",
             "import _prime  # noqa: F401\nfrom plug import paths\n\n\ndef A():\n    return paths.home()\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "", "src/plug/paths.py": "import functools\nimport os\n\n\n"
              "@functools.lru_cache(maxsize=None)\ndef home():\n    return os.environ['HOME']\n",
              "tests/_prime.py": "from plug import paths\n\npaths.home()\n"},
             f"{mod}: it no longer imports _prime, and importing _prime changes plug.paths.home"),
            # TJ-3966: the process's warnings filters; what lies below HOME, a
            # file a replay's unmoved fork writes there too (put back after it).
            ("the umask in code that stays", "A = 1\nK = os.umask(0o027)\n", "from test_src import A\n", {},
             f"{mod}: the process differs: umask"),
            ("a file made in code that stays",
             "A = 1\nK = os.close(os.open(os.path.expanduser('~/.tj-kept'), os.O_CREAT | os.O_WRONLY))\n",
             "from test_src import A\n", {}, f"{mod}: what lies below HOME differs: <HOME0>/.tj-kept"),
            ("a lost module's warnings filter", "import _q  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_q.py": "import warnings\n\nwarnings.filterwarnings('ignore', 'tj-quiet')\n"},
             f"{mod}: it no longer imports _q, and importing _q changes warnings"),
            ("a lost module that writes below HOME", "import _seed  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_seed.py": "import os\n\nwith open(os.path.expanduser('~/.plugcfg'), 'w') as f:\n"
                                "    f.write('1')\n"},
             f"{mod}: it no longer imports _seed, and importing _seed changes <HOME0>/.plugcfg"),
            ("a lost module that writes below the setup's HOME once", "import _seed  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_seed.py": "import os\n\nP = os.path.join(os.environ['TAXJSON_TEST_HOME'], 'once')\n"
                                "if not os.path.exists(P):\n    with open(P, 'w') as f:\n        f.write('1')\n",
              "tests/_hermetic.py": "import os\nimport tempfile\n\nos.environ['HOME'] = tempfile.mkdtemp()\n"
                                    "os.environ['TAXJSON_TEST_HOME'] = os.environ['HOME']\n"},
             f"{mod}: it no longer imports _seed, and importing _seed changes <HOME>/once"),
            # TJ-3967: a variable a test removes; a test's own constant, held
            # by a name; a value of its own for a number, for one not set, and
            # the offline switch's other spelling (TJ-3937).
            ("a variable a test removes", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    del os.environ['TAXJSON_WIDTH']\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nWIDE = 'TAXJSON_WIDTH' in os.environ\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (WIDE)"),
            ("a variable a test removes, the environment walked", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    del os.environ['TAXJSON_WIDTH']\n"
             "    import _cfg\n    return _cfg\n",
             # (no TAXJSON_ literal of its own: test_env would move it, TJ-3979)
             {"tests/_cfg.py": "import os\n\nKEYS = sorted(k for k in os.environ if k.endswith('_WIDTH'))\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (KEYS)"),
            ("a test's own value, by a name", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\nfrom unittest import mock\n\nfrom test_src import A\n\n\ndef later():\n"
             "    env = {'TJ_MODE_STATE': 'strict'}\n    with mock.patch.dict(os.environ, env):\n"
             "        import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nSTRICT = os.environ.get('TJ_MODE_STATE') == 'strict'\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (STRICT)"),
            ("values of their own", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later(w, n, m):\n    os.environ['TAXJSON_WIDTH'] = str(w)\n"
             "    os.environ['TJ_N_STATE'] = str(n)\n    os.environ['TAXJSON_OFFLINE'] = m\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nW = int(os.environ['TAXJSON_WIDTH'])\n"
                               "N = int(os.environ.get('TJ_N_STATE', '0'))\nMODE = os.environ.get('TAXJSON_OFFLINE')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (MODE, N, W)"),
            # A real importer whose HEAD import fails still may import it (TJ-3869).
            ("an importer whose HEAD import fails", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\nimport _boom  # noqa: F401\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_boom.py": "import sys\n\nif '_support' in sys.modules:\n    raise ImportError('boom')\n",
              "tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"},
             f"{mod}: tests/test_user.py (its import fails on HEAD) no longer loads _cfg, whose state moves "
             f"with the environment (WHERE)"),
            # TJ-3975: a store that replaces one of the product's cache wrappers.
            ("a lost module that replaces a cache memo", "import _stub  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {**TAXJSON, "src/taxjson/lib/markets_data.py": MARKETS,
              "tests/_stub.py": "from taxjson.lib import markets_data\n\n"
                                "markets_data.canadian_suffixes = lambda: frozenset({'TO'})\n"},
             f"{mod}: it no longer imports _stub, and importing _stub changes "
             f"taxjson.lib.markets_data.canadian_suffixes"),
            # TJ-3976: what a moved import changes of the process.
            ("a lost module's sys.path entry from a test's variable", "import _cfg  # noqa: F401\n\nA = 1\n",
             LATER_DEV, {"tests/_cfg.py": "import os\nimport sys\n\nif os.environ.get('TAXJSON_DEV_DIR'):\n"
                                          "    sys.path.insert(0, os.environ['TAXJSON_DEV_DIR'])\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (sys.path)"),
            ("a lost module's warnings filter from a test's variable", "import _cfg  # noqa: F401\n\nA = 1\n",
             LATER_DEV, {"tests/_cfg.py": "import os\nimport warnings\n\nif os.environ.get('TAXJSON_DEV_DIR'):\n"
                                          "    warnings.simplefilter('error')\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (warnings)"),
            ("a lost module's variable from a test's variable", "import _cfg  # noqa: F401\n\nA = 1\n",
             LATER_DEV, {"tests/_cfg.py": "import os\n\nif os.environ.get('TAXJSON_DEV_DIR'):\n"
                                          "    os.environ['TJ_SET_STATE'] = 'on'\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment "
             f"(environ TJ_SET_STATE)"),
            # TJ-3977: a cache of a function without parameters, by its one entry.
            ("a lost module's cache of HOME", "import _cfg  # noqa: F401\n\nA = 1\n", LATER,
             {"tests/_cfg.py": "import functools\nimport os\n\n\n" "@functools.lru_cache(maxsize=None)\n"
                               "def home():\n    return os.environ['HOME']\n\n\nhome()\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (home)"),
            # TJ-3978: a test's fifth constant (the cap was four).
            ("a test's fifth constant", "import _cfg  # noqa: F401\n\nA = 1\n", _constants("abcde"),
             {"tests/_cfg.py": "import os\n\nE = os.environ.get('TJ_K_STATE') == 'e'\n"},
             f"{mod}: tests/test_user.py no longer loads _cfg, whose state moves with the environment (E)"),
        ]
        for why, code, user, extra, want in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, files={"tests/test_src.py": HEADER + code,
                                                   "tests/test_user.py": user, **extra})
                self.assertTrue((r / "tests/_support/sup.py").is_file(), why)
                rc, out = _check(r, base, head)
                self.assertEqual(rc, 1, out)
                self.assertIn(want, out)
                self.assertNotIn("two runs of BASE's own import differ", out)   # TJ-3853

    def test_what_the_state_check_lets_pass(self):
        # TJ-3831, TJ-3833, TJ-3836: the hermetic setup first (a package),
        # its temp HOME by name in a value; a path entry made twice; a memo.
        cases = [
            ("a value holding the temp HOME", "H = os.environ['HOME']\n", "from test_src import H\n",
             {"tests/_hermetic/__init__.py": "import os\nimport tempfile\n\n"
                                             "os.environ['HOME'] = tempfile.mkdtemp()\n"}),
            ("an entry already on the path", "import _paths_x  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_paths_x.py": "import os\nimport sys\n\n"
                                   "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath("
                                   "__file__)), '..', 'src'))\n"}),
            ("an import with no effect", "import _plain  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_plain.py": "import json\n\nROWS = [1]\n"}),
            ("a named memo filled", "import _w  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"src/taxjson/__init__.py": "", "src/taxjson/lib/__init__.py": "",
              "src/taxjson/lib/once.py": "_SETS = []\n",
              "tests/_w.py": "from taxjson.lib import once\n\nonce._SETS.append(set())\n"}),
            # TJ-3975: a product cache wrapper a lost import fills (CACHED): its
            # entries are left out, the wrapper walked.
            ("a cache memo filled", "import _w  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {**TAXJSON, "src/taxjson/lib/markets_data.py": MARKETS,
              "tests/_w.py": "from taxjson.lib import markets_data\n\nmarkets_data.canadian_suffixes()\n"}),
            # TJ-4013: a moved cache wrapper (update_wrapper puts the place it
            # was defined in its own attributes) holds what it held.
            ("a moved cache", "import functools\n\n\n" "@functools.lru_cache(maxsize=None)\n"
             "def _docs():\n    return {'a': 1}\n", "from test_src import _docs\n", {}),
            # TJ-3869: a lost module whose state moves with HOME, which every
            # importer still loads; one that reads HOME, whose state does not.
            ("a module every importer loads", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import _cfg  # noqa: F401\nfrom test_src import A\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"}),
            ("a lost helper no importer can import in a test", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"}),
            ("a helper's HOME from the setup's record", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nENV = dict(os.environ, HOME=os.environ.get('TAXJSON_TEST_HOME', ''))\n"
                               "ENV = {k: v for k, v in ENV.items() if k == 'HOME'}\n",
              "tests/_hermetic/__init__.py": "import os\nimport tempfile\n\nos.environ['HOME'] = tempfile.mkdtemp()\n"
                                             "os.environ['TAXJSON_TEST_HOME'] = os.environ['HOME']\n"}),
            ("a file below the setup's own HOME", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nHAS = os.path.exists(os.path.join(os.environ.get('TAXJSON_TEST_HOME', '/'), '.cx'))\n",
              "tests/_hermetic/__init__.py": "import os\nimport tempfile\n\nos.environ['HOME'] = tempfile.mkdtemp()\n"
                                             "os.environ['TAXJSON_TEST_HOME'] = os.environ['HOME']\n"}),
            ("a read of HOME that keeps no trace", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_cfg.py": "import os\n\nHAS_HOME = 'HOME' in os.environ\n"}),
            # TJ-3904, TJ-3909: honest uses of the environment; a module the
            # setup serves under its own HOME (tests/_hermetic's CacheHome).
            ("the environment joined at import", "import _envu  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n", {"tests/_envu.py": "import os\n\nX = (os.environ | {}).get('TJ_NOPE')\n"}),
            ("the environment as a default", "import _run  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_run.py": "import os\n\n\ndef run(env=os.environ):\n    return env.get('X')\n"}),
            # TJ-3933, TJ-3937: the live environment as a default, a test may
            # import it; a variable no test sets; a module that refuses a
            # second import in a process (each replay is a fork's first).
            ("the environment as a default, imported in a test", "import _run  # noqa: F401\n\nA = 1\n",
             "import os\nfrom unittest import mock\n\nfrom test_src import A\n\n\ndef later():\n"
             "    with mock.patch.dict(os.environ, {'TJ_RUN_STATE': '1'}):\n        import _run\n    return _run\n",
             {"tests/_run.py": "import os\n\n\ndef run(env=os.environ):\n    return env.get('X')\n"}),
            ("a variable no test sets", "import _cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nV = os.environ.get('TJ_NOBODY_STATE')\n"}),
            ("offline stays on", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import os\n\nfrom test_src import A\n\n\ndef later():\n    os.environ['TAXJSON_OFFLINE'] = '1'\n"
             "    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nOFF = os.environ.get('TAXJSON_OFFLINE', '').lower() in "
                               "('1', 'true', 'yes', 'on')\n"}),
            ("a module that refuses a second import", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"src/plug/__init__.py": "", "src/plug/cfg.py": "import sys\n\nif getattr(sys, '_tj_seen', False):\n"
                                                             "    raise RuntimeError('twice')\nsys._tj_seen = True\n"}),
            ("a module that binds os.stat (watched while it is replayed)",
             "import _st  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n\n\ndef later():\n    import _st\n    return _st\n",
             {"tests/_st.py": "import os\nfrom os import access, lstat, stat  # noqa: F401\n\n"
                            "DEFAULT = {'stat': os.stat, 'access': os.access}\n"}),
            ("a module the setup serves under its own HOME", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_hermetic/__init__.py": PINNED_SETUP, "src/plug/__init__.py": "",
              "src/plug/cfg.py": READ_HOME_FILE + "WHERE = os.path.expanduser('~/x')\n"}),
            # TJ-3905, TJ-3907, TJ-3908: importers that still load it, each in
            # a child of its own, the package preloaded by the setup; one whose
            # BASE import did not load it either.
            ("two importers that still load it", "from plug import cfg  # noqa: F401\n\nA = 1\n",
             "from plug import cfg  # noqa: F401\nfrom test_src import A  # noqa: F401\n",
             {"tests/_hermetic/__init__.py": "import plug  # noqa: F401\n",
              "tests/test_u2.py": "from plug import cfg  # noqa: F401\nfrom test_src import A  # noqa: F401\n",
              "src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"}),
            ("an importer whose BASE import did not load it", "import _cfg  # noqa: F401\n\nA = 1\n",
             "import typing\n\nif typing.TYPE_CHECKING:\n    from test_src import A  # noqa: F401\n\n\n"
             "def later():\n    import _cfg\n    return _cfg\n",
             {"tests/_cfg.py": "import os\n\nWHERE = os.path.expanduser('~/x')\n"}),
            # TJ-3912: round five's survivor, a module outside the repository
            # below an outside package (os.path's shape) is no plant.
            ("an outside module aliased below an outside package", "import _al  # noqa: F401\n\nA = 1\n",
             "from test_src import A\n",
             {"tests/_al.py": "import decimal\nimport json  # noqa: F401\nimport sys\n\n"
                              "sys.modules['json.tj_alias'] = decimal\n"}),
            ("a package's own entry", "import _libfake  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_libfake.py": "import json\nimport sys\n\nsys.modules['json.tj_fake'] = 1\n"}),
            # TJ-3989: a variable a helper's import reads and no test sets.
            ("a variable the tests only read", "import _cfg  # noqa: F401\n\nA = 1\n", LATER,
             {"tests/_cfg.py": "import os\n\nN = int(os.environ.get('TAXJSON_FUZZ_TJ', '200'))\n"}),
            ("a package's own value that says it is a module (a cffi lib)",
             "import _libfake  # noqa: F401\n\nA = 1\n", "from test_src import A\n",
             {"tests/_libfake.py": "import json\nimport sys\nimport types\n\n\nclass Lib:\n"
                                   "    __class__ = property(lambda self: types.ModuleType)\n\n\n"
                                   "sys.modules['json.tj_lib'] = Lib()\n"}),
        ]
        for why, code, user, extra in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, files={"tests/test_src.py": HEADER + code,
                                                   "tests/test_user.py": user, **extra})
                rc, out = _check(r, base, head)
                self.assertEqual(rc, 0, out)

    def test_the_real_importers(self):
        # TJ-3905, TJ-3935, TJ-3938: a test module of tests/ or a package's
        # tests whose imports reach the source through test modules: at
        # module level, through a helper, a relative import (two levels up
        # too) or a namespace package; in a function, called at import or
        # not; by import_module or __import__ of a constant. Not one that
        # imports none, nor a package's test whose own folder has a module
        # of the source's name.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            for rel, text in {"tests/test_src.py": "A = 1\n", "tests/_h.py": "from test_src import A\n",
                              "tests/test_a.py": "import _h\n",
                              "tests/test_b.py": "def f():\n    import test_src\n",
                              "tests/sub/__init__.py": "", "tests/sub/_m.py": "import test_src\n",
                              "tests/sub/test_c.py": "from . import _m\n",
                              "tests/sub/deep/__init__.py": "", "tests/sub/deep/test_f.py": "from .. import _m\n",
                              "tests/ns/_n.py": "import test_src\n", "tests/test_g.py": "import ns._n\n",
                              "tests/_k.py": "def _get():\n    from test_src import A\n    return A\n\n\nA = _get()\n",
                              "tests/test_h.py": "import _k\n",
                              "tests/test_i.py": "import importlib\n\nM = importlib.import_module('test_src')\n",
                              "tests/test_j.py": "H = __import__('_h')\n",
                              "tests/test_k.py": "import json\n",
                              "packages/p/tests/test_d.py": "from test_src import A\n",
                              "packages/q/tests/test_src.py": "A = 2\n",
                              "packages/q/tests/test_e.py": "import test_src\n"}.items():
                (r / rel).parent.mkdir(parents=True, exist_ok=True)
                (r / rel).write_text(text, encoding="utf-8")
            self.assertEqual(ES.importers(r, "test_src"),
                             ["packages/p/tests/test_d.py", "tests/sub/deep/test_f.py", "tests/sub/test_c.py",
                              "tests/test_a.py", "tests/test_b.py", "tests/test_g.py", "tests/test_h.py",
                              "tests/test_i.py", "tests/test_j.py"])

    def test_an_importer_s_loads(self):
        # TJ-3938: a package's test is imported with its own folder first on
        # the path, so its own module wins over a same-named one in src/.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "r"
            for rel, text in {"packages/p/tests/test_pu.py": "import shadow_tj  # noqa: F401\n",
                              "packages/p/tests/shadow_tj.py": "X = 1\n", "src/shadow_tj.py": "X = 2\n",
                              "tests/x.py": ""}.items():
                (r / rel).parent.mkdir(parents=True, exist_ok=True)
                (r / rel).write_text(text, encoding="utf-8")
            home = Path(td) / "home"
            home.mkdir()
            got = ES.importer_loads(r, home, "packages/p/tests/test_pu.py", Path(td) / "out.json")
            self.assertIn("packages/p/tests/shadow_tj.py", got)
            self.assertNotIn("src/shadow_tj.py", got)

    def test_the_variables_the_tests_set(self):
        # TJ-3937: read from the tests and the packages' tests; the product's
        # TAXJSON_* names. TJ-3967: a dict a name holds, `dict(...)`; each
        # name's constants; the names removed, a clear's all but those kept.
        def tree(r, files):
            for rel, text in files.items():
                (r / rel).parent.mkdir(parents=True, exist_ok=True)
                (r / rel).write_text(text, encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            tree(r, {"tests/test_a.py": "import os\nfrom unittest import mock\n\n\ndef f(mp):\n"
                                        "    mock.patch.dict(os.environ, {'TJ_A': '1'}, TJ_B='2')\n"
                                        "    mock.patch.dict('os.environ', {'TJ_C': '1'}, clear=True)\n"
                                        "    os.environ['TJ_D'] = '1'\n    del os.environ['TJ_E']\n"
                                        "    os.environ.setdefault('TJ_F', '1')\n    os.environ.pop('TJ_G', None)\n"
                                        "    os.environ.update({'TJ_H': '1'}, TJ_I='1')\n    mp.setenv('TJ_J', '1')\n"
                                        "    os.putenv('TJ_K', '1')\n    env = {'TJ_NOT': '1'}\n    env['TJ_NOT2'] = '1'\n"
                                        "    os.environ['TJ_M'] += 'x'\n    env2 = {'TJ_O': 'o1'}\n"
                                        "    env2['TJ_P'] = 'p1'\n    mock.patch.dict(os.environ, env2)\n"
                                        "    mock.patch.dict(os.environ, dict(TJ_Q='q1'))\n",
                     "tests/test_c.py": "from os import environ\n\nenviron['TJ_N'] = '1'\n",
                     "packages/p/tests/test_b.py": "import os\n\nos.environ['TJ_L'] = '1'\n",
                     "packages/p/src/q.py": "X = 'TAXJSON_PKG'\n",
                     "src/m.py": "W = 'TAXJSON_WIDTH'\n"})
            names = ["TAXJSON_PKG", "TAXJSON_WIDTH", *(f"TJ_{c}" for c in "ABCDEFGHIJKLMNOPQ")]
            self.assertEqual(sorted(ES.test_env_names(r)), names)
            env = ES.test_env(r)
            self.assertEqual(env["deletes"], [n for n in names if n != "TJ_C"])     # the clear's
            self.assertEqual({k: env["values"].get(k) for k in ("TJ_B", "TJ_D", "TJ_F", "TJ_J", "TJ_O", "TJ_P",
                                                                 "TJ_Q", "TJ_NOT")},
                             {"TJ_B": ["2"], "TJ_D": ["1"], "TJ_F": ["1"], "TJ_J": ["1"], "TJ_O": ["o1"],
                              "TJ_P": ["p1"], "TJ_Q": ["q1"], "TJ_NOT": None})
        # TJ-3979: a dict the module binds, read in a function; the TAXJSON_*
        # names in the test files (a helper's dict).
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            tree(r, {"tests/test_a.py": "import os\nfrom unittest import mock\n\nENV = {'TJ_MOD': 'm1'}\n\n\n"
                                        "def f():\n    with mock.patch.dict(os.environ, ENV):\n        pass\n",
                     "tests/_h.py": "def env():\n    e = {}\n    e['TAXJSON_HELPER_X'] = '1'\n    return e\n"})
            env = ES.test_env(r)
            self.assertEqual(env["names"], ["TAXJSON_HELPER_X", "TJ_MOD"])
            self.assertEqual(env["values"], {"TJ_MOD": ["m1"]})
        # TJ-3989, TJ-4001, TJ-4007: a test file's TAXJSON_* string counts but
        # where it is read where it stands (R): each way it can be set or
        # removed (S); bound to a name, or an item on the right of `in`, it
        # counts however the file reads it (B: over-counted, failing closed).
        sets = {"a dict key": "E = {'TAXJSON_S_KEY': '1'}\n", "a keyword": "E = dict(TAXJSON_S_KW='1')\n",
                "a subscript store": "e['TAXJSON_S_STORE'] = '1'\n",
                "an added store": "e['TAXJSON_S_ADD'] += '1'\n",
                "a typed store": "e['TAXJSON_S_TYPED']: str = '1'\n",
                "setdefault": "e.setdefault('TAXJSON_S_SETDEFAULT', '1')\n",
                "update": "e.update(TAXJSON_S_UPDATE='1')\n", "update's dict": "e.update({'TAXJSON_S_UPD': '1'})\n",
                "patch.dict": "mock.patch.dict(os.environ, TAXJSON_S_PATCH='1')\n",
                "mock.patch.dict": "unittest.mock.patch.dict('os.environ', {'TAXJSON_S_MOCK': '1'})\n",
                "os.environ": "os.environ['TAXJSON_S_ENVIRON'] = '1'\n", "putenv": "os.putenv('TAXJSON_S_PUTENV', '1')\n",
                "setenv": "mp.setenv('TAXJSON_S_SETENV', '1')\n", "del": "del e['TAXJSON_S_DEL']\n",
                "pop": "e.pop('TAXJSON_S_POP', None)\n", "delenv": "mp.delenv('TAXJSON_S_DELENV')\n",
                "unsetenv": "os.unsetenv('TAXJSON_S_UNSETENV')\n",
                "by a name": "V = 'TAXJSON_S_NAMED'\ne[V] = '1'\n",
                "by a loop": "for k in ('TAXJSON_S_LOOP',):\n    e.pop(k, None)\n",
                "by a loop over a name": "KS = ['TAXJSON_S_KS']\nfor k2 in KS:\n    e[k2] = '1'\n",
                "a key by a name": "E2 = {V3: '1' for V3 in ('TAXJSON_S_COMP',)}\nE3 = {V: '1'}\n",
                "a loop over pairs": "for k5, v5 in (('TAXJSON_S_PAIRLOOP', '1'),):\n    os.environ[k5] = v5\n",
                "names unpacked": "K6, K7 = 'TAXJSON_S_UNPACK_A', 'TAXJSON_S_UNPACK_B'\n"
                                  "os.environ[K6] = os.environ[K7] = '1'\n",
                "a walrus": "if (k8 := 'TAXJSON_S_WALRUS'):\n    os.environ[k8] = '1'\n",
                "an annotated name": "K9: str = 'TAXJSON_S_ANNOTATED'\nos.environ[K9] = '1'\n",
                "update's pairs": "os.environ.update([('TAXJSON_S_PAIRS', '1')])\n",
                "dict.fromkeys": "e.update(dict.fromkeys(['TAXJSON_S_FROMKEYS'], ''))\n",
                "zip": "E4 = dict(zip(['TAXJSON_S_ZIP'], ['1']))\n",
                "a helper given the name": "_set('TAXJSON_S_HELPER', '1')\n",
                "two stores": "os.environ['TAXJSON_S_TWO_A'], os.environ['TAXJSON_S_TWO_B'] = '1', '2'\n",
                "environb": "os.environb[b'TAXJSON_S_ENVIRONB'] = b'1'\n",
                "putenv's bytes": "os.putenv(b'TAXJSON_S_PUTENVB', b'1')\n"}
        bound = {"a name read": "M = 'TAXJSON_B_BOUND'\nassert M in os.environ\n",
                 "a loop's name read": "for k11 in ('TAXJSON_B_LOOP',):\n    os.environ.get(k11)\n",
                 "a walrus read": "if (k12 := 'TAXJSON_B_WALRUS'):\n    os.environ.get(k12)\n",
                 "an annotated name read": "K13: str = 'TAXJSON_B_ANNOTATED'\nos.getenv(K13)\n",
                 "a name unpacked, read": "K14, K15 = 'TAXJSON_B_UNPACK', '1'\nos.environ.get(K14)\n",
                 "a comprehension's name read": "C = [os.environ.get(k16) for k16 in ('TAXJSON_B_COMP',)]\n",
                 "an item on the right of in": "L = e in ('TAXJSON_B_TUPLE',)\n",
                 "the right operand of in": "L2 = 'X' in 'TAXJSON_B_RIGHT'\n"}
        reads = ("N = int(os.environ.get('TAXJSON_R_GET', '200'))\nK = os.environ['TAXJSON_R_SUB']\n"
                 "I = 'TAXJSON_R_IN' in os.environ\nG = os.getenv('TAXJSON_R_GETENV')\n# TAXJSON_R_COMMENT\n"
                 "X = 'TAXJSON_R_MSG is unset'\nY = e['TAXJSON_R_LOAD']\n"
                 "N2 = os.environb.get(b'TAXJSON_R_GETB')\nJ = 'TAXJSON_R_NOTIN' not in os.environ\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            tree(r, {"tests/test_a.py": "import os\nimport unittest\nfrom unittest import mock\n\ne = {}\n"
                                        + reads + "".join(sets.values()) + "".join(bound.values())})
            names = ES.test_env(r)["names"]
        self.assertEqual([n for n in names if "_R_" in n], [])
        both = {**sets, **bound}
        for why, text in both.items():
            with self.subTest(why):
                self.assertTrue(set(re.findall(r"TAXJSON_[SB]_[A-Z_]+", text)) <= set(names), names)
        self.assertEqual(len(names), len({s for x in both.values() for s in re.findall(r"TAXJSON_[SB]_[A-Z_]+", x)}))
        # TJ-4007: a name another module imports and sets; a class attribute
        # set through self, the same id elsewhere only read; the items a
        # helper's filter keeps out of a cleared environment.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            tree(r, {"tests/_envnames.py": "import os\n\nK = 'TAXJSON_S_IMPORTED'\n\n\ndef present():\n"
                                           "    return K in os.environ\n",
                     "tests/test_a.py": "import os\nfrom _envnames import K\n\n\ndef setUpModule():\n"
                                        "    os.environ[K] = '1'\n",
                     "tests/test_b.py": "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                                        "    K = 'TAXJSON_S_ATTR'\n\n    def setUp(self):\n"
                                        "        os.environ[self.K] = '1'\n\n\ndef other():\n"
                                        "    K = 'TAXJSON_B_OTHER'\n    return os.getenv(K)\n",
                     "tests/_envh.py": "import os\n\n\ndef without():\n    return {k: v for k, v in os.environ.items()"
                                       " if k not in ('TAXJSON_S_DROPPED',)}\n",
                     "tests/test_c.py": "import os\nfrom unittest import mock\nfrom _envh import without\n\n\n"
                                        "def test():\n    with mock.patch.dict(os.environ, without(), clear=True):\n"
                                        "        pass\n"})
            env = ES.test_env(r)
        self.assertEqual(env["names"], ["TAXJSON_B_OTHER", "TAXJSON_S_ATTR", "TAXJSON_S_DROPPED", "TAXJSON_S_IMPORTED"])
        self.assertIn("TAXJSON_S_DROPPED", env["deletes"])
        cases = [
            ("removed", "import os\nfrom unittest import mock\n\n\ndef f(mp):\n"
             "    keep = {k: v for k, v in os.environ.items()\n"
             "            if k != 'TJ_R' and k not in ('TJ_S',) and not k.startswith('TJ_T')\n"
             "            and k not in ['TJ_U']}\n"
             "    mock.patch.dict(os.environ, keep, clear=True)\n    os.environ['TJ_T1'] = 't'\n"
             "    keep2 = {k: os.environ[k] for k in os.environ.keys() if k != 'TJ_V2'}\n"
             "    mock.patch.dict(os.environ, keep2, clear=True)\n"
             "    del os.environ['TJ_E']\n    os.environ.pop('TJ_G', None)\n    mp.delenv('TJ_H')\n"
             "    os.unsetenv('TJ_I')\n    os.environ['TJ_W'] = 'w'\n"
             "    mock.patch.dict(os.environ, {**os.environ, 'TJ_X': 'x'}, clear=True)\n",
             ["TJ_E", "TJ_G", "TJ_H", "TJ_I", "TJ_R", "TJ_S", "TJ_T1", "TJ_U", "TJ_V2"]),
            ("a clear it cannot read", "import os\nfrom unittest import mock\n\n\ndef f(env):\n"
             "    mock.patch.dict(os.environ, env, clear=True)\n    os.environ['TJ_Y'] = 'y'\n", ["TJ_Y"]),
            ("os.environ.clear()", "import os\n\nos.environ.clear()\nos.environ['TJ_Z'] = 'z'\n", ["TJ_Z"]),
        ]
        for why, text, deletes in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                tree(Path(td), {"tests/test_a.py": text})
                self.assertEqual(ES.test_env(Path(td))["deletes"], deletes)

    def test_a_package_s_own_effects(self):
        # A module outside the repository whose import sets something: what
        # it sets is that package's, not the move's. Imported first, before
        # the dropped module of the repository that imports it, and loaded
        # on HEAD when only BASE's source imported it.
        cases = [("through a dropped module", "import _x  # noqa: F401\n\nA = 1\n",
                  {"tests/_x.py": "import zext_tj  # noqa: F401\n"}),
                 ("by the source itself", "import zext_tj  # noqa: F401\n\nA = 1\n", {}),
                 ("through a guarded from-import", "import _x  # noqa: F401\n\nA = 1\n",
                  {"tests/_x.py": "try:\n    from zext_tj import X  # noqa: F401\nexcept ImportError:\n"
                                  "    X = None\n"}),
                 # TJ-3938: the fallback an except clause imports
                 ("through a guarded import's fallback", "import _x  # noqa: F401\n\nA = 1\n",
                  {"tests/_x.py": "try:\n    import zext_missing_tj  # noqa: F401\nexcept ImportError:\n"
                                  "    import zext_tj  # noqa: F401\n"})]
        for why, code, extra in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r, ext = Path(td) / "r", Path(td) / "ext"
                r.mkdir()
                ext.mkdir()
                (ext / "zext_tj.py").write_text("import os\n\nos.environ['TJ_ZEXT_STATE'] = '1'\n")
                base, head = _check_repo(r, files={"tests/test_src.py": HEADER + code,
                                                   "tests/test_user.py": "from test_src import A\n", **extra})
                env = ES._child_env
                with mock.patch.object(ES, "_child_env",
                                       lambda tree, home, extra="": env(tree, home, extra or str(ext))):
                    rc, out = _check(r, base, head)
                self.assertEqual(rc, 0, out)

    def test_a_snapshot_past_the_timeout(self):
        # TJ-3856: a check line, no traceback.
        with tempfile.TemporaryDirectory() as td:
            b, home = Path(td) / "b", Path(td) / "home"
            (b / "tests/_support").mkdir(parents=True)
            (b / "tests/_support/sup.py").write_text("A = 1\n")
            (b / "tests/test_src.py").write_text("import time\n\ntime.sleep(5)\nA = 1\n")
            home.mkdir()
            with mock.patch.object(ES, "TIMEOUT", 0.5):
                found = ES.state_check(b, b, home, [("test_src", "sup", "x")], Path(td))
            self.assertTrue(any("the snapshot took over 0.5 s (TIMEOUT)" in x for x in found), found)

    def test_a_replay_that_cannot_run(self):
        # TJ-3933: a replay that hangs is killed, and one without os.fork
        # cannot run: each fails closed.
        mod = "state: an importer of test_src's moved names"
        for why, old, new in (("past its timeout", "FORK_TIMEOUT = 300", "FORK_TIMEOUT = 1"),
                              ("no os.fork", 'hasattr(os, "fork")', 'hasattr(os, "fork_tj_none")')):
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, files={
                    "tests/test_src.py": HEADER + "import _cfg  # noqa: F401\n\nA = 1\n",
                    "tests/test_user.py": "import os\n\nfrom test_src import A\n\n\ndef later():\n"
                                          "    os.environ['TJ_SLOW_STATE'] = '1'\n    import _cfg\n    return _cfg\n",
                    "tests/_cfg.py": "import os\nimport time\n\nif os.environ.get('TJ_SLOW_STATE'):\n"
                                     "    time.sleep(60)\n"})
                self.assertEqual(ES._STATE.count(old), 1)
                with mock.patch.object(ES, "_STATE", ES._STATE.replace(old, new)):
                    rc, out = _check(r, base, head)
                self.assertEqual(rc, 1, out)
                self.assertIn(f"{mod}: tests/test_user.py no longer loads _cfg, whose state may move with the "
                              f"environment (its replay fails, so the check cannot tell)", out)

    def test_what_a_moved_import_touches(self):
        # TJ-3964: each path below the moved HOME an import makes, removes,
        # renames, links, changes, enters, lists, globs, opens or looks at
        # (a file that is not there too), each path argument.
        ops = {"mkdir": "os.mkdir(P('mkdir'))", "makedirs/sub": "os.makedirs(P('makedirs/sub'))",
               "path-mkdir": "pathlib.Path(P('path-mkdir')).mkdir()", "rmdir": "os.rmdir(P('rmdir'))",
               "unlink": "os.unlink(P('unlink'))", "path-unlink": "pathlib.Path(P('path-unlink')).unlink()",
               "renamed": "os.rename(P('rename'), P('renamed'))", "replace": "os.replace(P('replace'), P('replaced'))",
               "linked": "os.link(P('link'), P('linked'))", "symlink": "os.symlink('target', P('symlink'))",
               "chmod": "os.chmod(P('chmod'), 0o600)", "utime": "os.utime(P('utime'))",
               "truncate": "os.truncate(P('truncate'), 0)",
               "chdir": "here = os.getcwd()\nos.chdir(P('chdir'))\nos.chdir(here)",
               "copyfile": "shutil.copyfile(P('copyfile'), P('copied'))",
               "copytree": "shutil.copytree(P('copytree'), P('copiedtree'))", "rmtree": "shutil.rmtree(P('rmtree'))",
               "moved": "shutil.move(P('move'), P('moved'))",
               "mkdtemp-": "tempfile.mkdtemp(prefix='mkdtemp-', dir=H)",
               "mkstemp-": "os.close(tempfile.mkstemp(prefix='mkstemp-', dir=H)[0])",
               "glob": "glob.glob(P('glob') + '/*')", "path-glob": "list(pathlib.Path(P('path-glob')).glob('*'))",
               "iterdir": "list(pathlib.Path(P('iterdir')).iterdir())", "walk": "list(os.walk(P('walk')))",
               "listdir": "os.listdir(P('listdir'))", "scandir": "list(os.scandir(P('scandir')))",
               "touch": "pathlib.Path(P('touch')).touch()", "open": "open(P('open'), 'w').close()",
               "readlink": "os.readlink(P('readlink'))", "statvfs": "os.statvfs(P('statvfs'))",
               "mkfifo": "os.mkfifo(P('mkfifo'))", "exists": "os.path.exists(P('exists'))",
               "path-exists": "pathlib.Path(P('path-exists')).exists()",
               "realpath": "os.path.realpath(P('realpath'))", "access": "os.access(P('access'), os.R_OK)",
               "lexists": "os.path.lexists(P('lexists'))", "sqlite": "sqlite3.connect(P('sqlite')).close()",
               "globroot": "glob.glob('*', root_dir=P('globroot'))"}
        child = ("import json, os, sys\nns = {'__name__': 'tj_defs'}\nexec(open(sys.argv[1]).read(), ns)\n"
                 "sys.addaudithook(ns['_audit'])\nsys.path.insert(0, sys.argv[3])\n"
                 "print(json.dumps(ns['replayed']('_ops', sys.argv[2], {'HOME': sys.argv[2]})['touched']))\n")
        with tempfile.TemporaryDirectory() as td:
            home, mods = Path(td) / "home", Path(td) / "mods"
            for d in ("rmdir", "chdir", "copytree", "rmtree/x", "glob", "path-glob", "iterdir", "walk",
                      "listdir", "scandir", "statvfs", "globroot"):
                (home / d).mkdir(parents=True)
            for f in ("unlink", "path-unlink", "rename", "replace", "link", "chmod", "utime", "truncate",
                      "copyfile", "move"):
                (home / f).write_text("x")
            (home / "readlink").symlink_to("x")
            mods.mkdir()
            (mods / "_ops.py").write_text(
                "import glob, os, pathlib, shutil, sqlite3, tempfile\n\nH = os.environ['HOME']\n\n\n"
                "def P(n):\n    return os.path.join(H, n)\n\n\n" + "\n".join(ops.values()) + "\n")
            (mods / "defs.py").write_text(ES._SNAP + ES._STATE_DEFS)
            r = subprocess.run([sys.executable, "-I", "-c", child, str(mods / "defs.py"), str(home), str(mods)],
                               capture_output=True, text=True, check=True)
            got = json.loads(r.stdout)
        for tag in ops:
            with self.subTest(tag):
                self.assertTrue(any(p == f"~/{tag}" or (tag.endswith("-") and p.startswith(f"~/{tag}"))
                                    for p in got), got)

    def test_a_fork_s_own_processes(self):
        # TJ-3970: a fork leads a session of its own: what its import
        # started dies with it, at a timeout and when it ends.
        if not Path("/proc").is_dir():
            self.skipTest("no /proc")
        child = ("import json, os, subprocess, sys, time\nns = {'__name__': 'tj_defs'}\n"
                 "exec(open(sys.argv[1]).read(), ns)\nns['FORK_TIMEOUT'] = 2\n\n\n"
                 "def start(hang):\n    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                 "    with open(sys.argv[2], 'a') as f:\n        f.write(f'{p.pid}\\n')\n"
                 "    if hang:\n        time.sleep(60)\n    return os.getpgid(0) == os.getpid()\n\n\n"
                 "print(json.dumps([ns['forked'](start, False), ns['forked'](start, True)]))\n")

        def alive(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
            except (OSError, IndexError):
                return False
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "defs.py").write_text(ES._SNAP + ES._STATE_DEFS)
            r = subprocess.run([sys.executable, "-I", "-c", child, str(Path(td) / "defs.py"),
                                str(Path(td) / "pids")], capture_output=True, text=True, check=True, timeout=60)
            self.assertEqual(json.loads(r.stdout), [True, None])
            pids = [int(x) for x in (Path(td) / "pids").read_text().split()]
        self.assertEqual(len(pids), 2)
        for _ in range(50):
            if not any(alive(p) for p in pids):
                break
            time.sleep(0.1)
        self.assertEqual([p for p in pids if alive(p)], [])

    def test_the_replay_s_forks(self):
        # TJ-3967: the forks a replay runs, and with what: the moved one;
        # one with the removed names unset when the import reads one or
        # walks the environment; one per constant of a name it reads; never
        # HOME, the setup's record or the offline switch unset or given a
        # test's value. TJ-3933: a noise fork that fails fails it.
        ns: dict = {}
        exec(ES._SNAP + ES._STATE_DEFS, ns)
        ns["SUBS"] = []
        again = ns["AGAIN"]

        def run(results, envs):
            calls = []

            def stub(fn, name, home, env):
                calls.append(dict(env))
                return results.pop(0) if results else None
            ns["forked"] = stub
            return ns["replay"](["_x"], envs), calls

        def fork(x="1", reads=()):
            return {"rows": {"_x": {"X": x}}, "touched": [], "reads": list(reads)}
        got, calls = run([fork(), fork("2"), None], {"names": []})
        self.assertEqual((got, len(calls)), ({"_x": [again]}, 3))
        envs = {"names": ["TJ_A", "TAXJSON_OFFLINE"],
                "deletes": ["TJ_GONE", "HOME", "TAXJSON_OFFLINE", "TAXJSON_TEST_HOME"],
                "values": {"TJ_V": ["p", "q", "r", "s", "t"], "TAXJSON_OFFLINE": ["0", ""]}}
        got, calls = run([fork(), fork()], envs)
        self.assertEqual((got, len(calls), calls[0]), ({}, 2, {}))
        self.assertEqual(calls[1]["TJ_A"], "1")
        self.assertTrue(calls[1]["HOME"] and calls[1]["TAXJSON_OFFLINE"] not in ("", "0"))
        got, calls = run([fork(reads=["TJ_GONE"]), fork(), fork()], envs)
        self.assertEqual((got, len(calls)), ({}, 3))
        self.assertEqual([k for k, v in calls[2].items() if v is None], ["TJ_GONE"])
        ns["CONSTANT_FORKS"] = 4         # TJ-3978: the cap, and the constants it leaves out
        got, calls = run([fork(reads=["*"])] + [fork()] * 6, envs)
        self.assertEqual((got, len(calls)), ({}, 2 + 1 + 4))
        self.assertEqual([c["TJ_V"] for c in calls[3:]], ["p", "q", "r", "s"])
        self.assertEqual(ns["UNTRIED"], {"TJ_V": ["t"]})
        self.assertTrue(all(c["TAXJSON_OFFLINE"] not in ("", "0") for c in calls[1:]))
        got, calls = run([fork(reads=["TJ_V"]), fork(), fork(), fork("2"), fork(), fork(), fork()], envs)
        self.assertEqual((got, len(calls)), ({"_x": ["X"]}, 7))
        ns["CONSTANT_FORKS"] = ES.CONSTANT_FORKS
        ns["UNTRIED"].clear()
        got, calls = run([fork(reads=["TJ_V"])] + [fork()] * 6, envs)
        self.assertEqual((got, len(calls), ns["UNTRIED"]), ({}, 2 + 5, {}))
        self.assertEqual([c["TJ_V"] for c in calls[2:]], ["p", "q", "r", "s", "t"])
        ns["CONSTANT_FORKS"] = 5         # TJ-3990: as many as the cap: none left out, no note
        got, calls = run([fork(reads=["TJ_V"])] + [fork()] * 6, envs)
        self.assertEqual((got, len(calls), ns["UNTRIED"]), ({}, 2 + 5, {}))
        ns["CONSTANT_FORKS"] = ES.CONSTANT_FORKS

        # TJ-3976: a view of the process a moved import changes moves it, but
        # not one two unmoved forks differ in as well.
        def viewed(path, environ=(), decimal=(), process=None, fakes=None):
            return {**fork(), "views": {"process": process or {"umask": "0o22"}, "path": path,
                                        "decimal": list(decimal), "environ": list(environ),
                                        "fakes": fakes or {}}}
        got, _calls = run([viewed(["a"]), viewed(["b"]), viewed(["a"])], {"names": []})
        self.assertEqual(got, {"_x": ["sys.path"]})
        got, _calls = run([viewed(["a"]), viewed(["b"]), viewed(["c"])], {"names": []})
        self.assertEqual(got, {})
        got, _calls = run([viewed(["a"]), viewed(["a"], ["TJ_Y=1"]), viewed(["a"])], {"names": []})
        self.assertEqual(got, {"_x": ["environ TJ_Y"]})
        got, _calls = run([viewed(["a"]), viewed(["b"]), None], {"names": []})
        self.assertEqual(got, {"_x": [again]})
        # TJ-3990: the decimal context; the noise rule per view, and per key of
        # the process's; TJ-3991: a fake module.
        got, _calls = run([viewed(["a"]), viewed(["a"], decimal=[29]), viewed(["a"])], {"names": []})
        self.assertEqual(got, {"_x": ["the decimal context"]})
        got, _calls = run([viewed(["a"]), viewed(["b"], process={"umask": "0o77"}),
                           viewed(["a"], process={"umask": "0o77"})], {"names": []})
        self.assertEqual(got, {"_x": ["sys.path"]})
        got, _calls = run([viewed(["a"], process={"umask": "0o22", "warnings": []}),
                           viewed(["a"], process={"umask": "0o77", "warnings": ["x"]}),
                           viewed(["a"], process={"umask": "0o22", "warnings": ["y"]})], {"names": []})
        self.assertEqual(got, {"_x": ["umask"]})
        got, _calls = run([viewed(["a"]), viewed(["a"], fakes={"tj_fake": "1"}), viewed(["a"])], {"names": []})
        self.assertEqual(got, {"_x": ["sys.modules['tj_fake']"]})
        f1, f2 = {"tj_fake": "1"}, {"tj_fake": "2"}       # TJ-4002: the same fake, changed
        got, _calls = run([viewed(["a"], fakes=f1), viewed(["a"], fakes=f2), viewed(["a"], fakes=f1)], {"names": []})
        self.assertEqual(got, {"_x": ["sys.modules['tj_fake']"]})

    def test_the_process_view(self):
        # TJ-3966: what the world compares of the process beyond the
        # modules: each change is seen by its own key; what lies below HOME.
        import logging
        import warnings
        ns: dict = {}
        exec(ES._SNAP + ES._STATE_DEFS, ns)
        view = ns["process_view"]
        before = view()

        class Finder:
            def find_spec(self, *a):
                return None

        def hook(path):
            raise ImportError(path)
        def shown(*a, **k):
            return None
        handler, mask, off = logging.NullHandler(), [0], logging.root.manager.disable
        level = logging.root.level
        changes = [
            ("warnings", lambda: warnings.filterwarnings("ignore", "tj-x"), lambda: None),
            ("warnings", lambda: setattr(warnings, "showwarning", shown), lambda: None),   # TJ-3980
            ("logging", lambda: logging.root.setLevel(1), lambda: logging.root.setLevel(level)),
            ("meta_path", lambda: sys.meta_path.insert(0, Finder()), lambda: sys.meta_path.pop(0)),
            ("path_hooks", lambda: sys.path_hooks.append(hook), lambda: sys.path_hooks.remove(hook)),
            ("logging", lambda: logging.disable(logging.CRITICAL), lambda: logging.disable(off)),
            ("logging", lambda: logging.root.addHandler(handler), lambda: logging.root.removeHandler(handler)),
            ("umask", lambda: mask.__setitem__(0, os.umask(0o077)), lambda: os.umask(mask[0])),
        ]
        for key, do, undo in changes:
            with self.subTest(key), warnings.catch_warnings():
                do()
                try:
                    after = view()
                finally:
                    undo()
                self.assertEqual([k for k in before if before[k] != after[k]], [key])
        self.assertEqual(view(), before)
        # TJ-3980, TJ-3981: a filter's module; the filters' order (the first
        # that fits wins).
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", module="tj_a")
            one = view()
            warnings.filters.pop(0)
            warnings.filterwarnings("ignore", module="tj_b")
            self.assertNotEqual(view()["warnings"], one["warnings"])
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "tj-1")
            warnings.filterwarnings("ignore", "tj-2")
            one = view()
            warnings.filterwarnings("ignore", "tj-1")       # to the front
            two = view()
            self.assertEqual(sorted(two["warnings"]), sorted(one["warnings"]))
            self.assertNotEqual(two["warnings"], one["warnings"])
        with tempfile.TemporaryDirectory() as td:
            ns["SUBS"] = [(td, "<HOME>")]
            (Path(td) / "d").mkdir()
            (Path(td) / "d/f").write_text("abc")
            self.assertEqual(ns["home_view"](), ["<HOME>/d/", "<HOME>/d/f 3"])
            self.assertFalse(ns["home_cut"]["cut"])
            ns["HOME_MAX"] = 1                  # TJ-3981: past it, cut and said
            self.assertEqual(ns["home_view"](), ["<HOME>/d/", "<cut>"])
            self.assertTrue(ns["home_cut"]["cut"])

    def test_what_lies_below_home(self):
        # TJ-3980: a removal below HOME is a change of the world; the HOMEs
        # are put back after a fork that made a folder or rewrote a file
        # (its size kept, its time not).
        ns: dict = {}
        exec(ES._SNAP + ES._STATE_DEFS, ns)
        w = {"environ": [], "path": {}, "decimal": [], "random": "", "process": {},
             "home": ["<HOME>/x 1"], "fakes": {}, "mods": {}}
        self.assertEqual(ns["changed"](w, {**w, "home": []}), ["<HOME>/x"])
        self.assertEqual(ns["changed"]({**w, "home": []}, w), ["<HOME>/x"])
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            ns["SUBS"] = [(td, "<HOME>")]
            (top / "f").write_text("abc")
            homes = ns["Homes"]()
            try:
                (top / "d").mkdir()
                (top / "d/g").write_text("1")
                homes.back()
                self.assertEqual(sorted(p.name for p in top.iterdir()), ["f"])
                (top / "f").write_text("xyz")
                os.utime(top / "f", ns=(10**9, 10**9))
                homes.back()
                self.assertEqual((top / "f").read_text(), "abc")
            finally:
                homes.drop()

    def test_a_replay_s_reads_and_views(self):
        # TJ-3980: environb's names read as text; TJ-3976: the views a
        # replay takes after the import (what it changed of the environment:
        # a variable removed too, TJ-3990; the fake modules, TJ-3991, one there
        # before the import that it changes too, TJ-4002, against an import
        # that leaves it).
        child = ("import json, os, sys, types\nns = {'__name__': 'tj_defs'}\nexec(open(sys.argv[1]).read(), ns)\n"
                 "ns['SUBS'] = []\nsys.path.insert(0, sys.argv[2])\n"
                 "sys.modules['tj_fake_before'] = types.ModuleType('tj_fake_before')\n"
                 "r0 = ns['replayed']('_plain', '', {})\n"
                 "r = ns['replayed']('_envb', '', {'TJ_B_STATE': 'b', 'TJ_D_STATE': 'd'})\n"
                 "print(json.dumps([r['reads'], r['views']['environ'], r['views']['path']['entries'],\n"
                 "                  sorted(r['views']['fakes']), r0['views']['fakes'].get('tj_fake_before'),\n"
                 "                  r['views']['fakes'].get('tj_fake_before')]))\n")
        with tempfile.TemporaryDirectory() as td:
            mods = Path(td) / "mods"
            mods.mkdir()
            (mods / "_envb.py").write_text("import os\nimport sys\nimport types\n\nB = os.environb.get(b'TJ_B_STATE')\n"
                                           "os.environ['TJ_C_STATE'] = '1'\nsys.path.append('/tj-views-x')\n"
                                           "del os.environ['TJ_D_STATE']\n"
                                           "sys.modules['tj_fake_views'] = types.ModuleType('tj_fake_views')\n"
                                           "sys.modules['tj_fake_before'].X = 1\n")
            (mods / "_plain.py").write_text("X = 1\n")
            (mods / "defs.py").write_text(ES._SNAP + ES._STATE_DEFS)
            r = subprocess.run([sys.executable, "-I", "-c", child, str(mods / "defs.py"), str(mods)],
                               capture_output=True, text=True, check=True)
            reads, environ, entries, fakes, left, changed = json.loads(r.stdout)
        self.assertEqual(reads, ["TJ_B_STATE"])
        self.assertEqual(environ, ["TJ_C_STATE=1", "-TJ_D_STATE=d"])
        self.assertIn("/tj-views-x", entries)
        self.assertIn("tj_fake_views", fakes)
        self.assertTrue(left and changed and left != changed, (left, changed))

    def test_the_check_s_notes(self):
        # TJ-3978: the constants a replay leaves out above CONSTANT_FORKS
        # are named; TJ-3981: so is a listing below HOME cut short.
        cap = ES.CONSTANT_FORKS
        cs = "abcdefghijklmnop"[:cap + 1]
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={
                "tests/test_src.py": HEADER + "import _cfg  # noqa: F401\n\nA = 1\n",
                "tests/test_user.py": _constants(cs),
                "tests/_cfg.py": "import os\n\nE = os.environ.get('TJ_K_STATE') == 'z'\n"})
            rc, out = _check(r, base, head)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"check: note: test_src-sup: a replay gave TJ_K_STATE {cap} of the tests' constants "
                      f"(CONSTANT_FORKS), not {cs[-1]}", out)
        real = ES.state

        def cut(*a, **k):
            return {**real(*a, **k), "home_cut": True}
        with tempfile.TemporaryDirectory() as td, mock.patch.object(ES, "state", cut):
            r = Path(td)
            base, head = _check_repo(r)
            rc, out = _check(r, base, head)
        self.assertEqual(rc, 0, out)
        self.assertIn("check: note: test_src-base: what lies below HOME passed HOME_MAX entries; past them "
                      "it is not compared", out)

    def test_the_cap_covers_the_tree_s_constants(self):
        # TJ-3978: no variable of this tree's tests has more constants than
        # a replay tries.
        values = ES.test_env(_paths.REPO)["values"]
        self.assertTrue(values)
        self.assertLessEqual(max(len(vs) for vs in values.values()), ES.CONSTANT_FORKS, values)

    def test_the_clock_is_the_suite_s(self):
        # TJ-3933: what differs between two replays that move nothing is not
        # a move; two runs of BASE's own import that differ fail the check.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={
                "tests/test_src.py": HEADER + "import _cfg  # noqa: F401\n\nA = 1\n",
                "tests/test_user.py": "from test_src import A\n\n\ndef later():\n    import _cfg\n    return _cfg\n",
                "tests/_cfg.py": "import time\n\nT = time.time()\n"})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("two runs of BASE's own import differ, so the check cannot compare them: _cfg:T", out)
            self.assertNotIn("moves with the environment", out)

    def test_the_ids_run_after_the_hermetic_package(self):
        # TJ-3852: discovery's process imports the hermetic setup first, a
        # package as well as a file.
        need = ("import sys\nimport unittest\n\nassert '_hermetic' in sys.modules\n\n\n"
                "class TestN(unittest.TestCase):\n    def test_n(self):\n        pass\n")
        for setup in ("tests/_hermetic/__init__.py", "tests/_hermetic.py"):   # (discovery imports a package)
            with self.subTest(setup), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, files={setup: "", "tests/test_needs.py": need})
                out = io.StringIO()
                with redirect_stdout(out), redirect_stderr(out):
                    rc = ES.check(r, base, head, [], False)
                self.assertEqual(rc, 0, out.getvalue())

    def test_the_named_memos_exist(self):
        # TJ-3842: each memo the lost-import check leaves out is still there.
        import importlib
        ns: dict = {}
        exec(ES._SNAP, ns)
        self.assertTrue(ns["MEMOS"])
        for name, why in ns["MEMOS"].items():
            mod, _, attr = name.rpartition(".")
            with self.subTest(name):
                self.assertTrue(why)
                self.assertTrue(hasattr(importlib.import_module(mod), attr), name)

    def test_every_cache_of_the_product_is_a_memo(self):
        # TJ-3870: a module-level cache of the product's (a name that says
        # cache, an empty container or None; TJ-3965: a function a cache
        # wrapper decorates) is in MEMOS, so a new one is named there (or
        # renamed) before a lost import that fills it fails.
        import ast
        ns: dict = {}
        exec(ES._SNAP, ns)
        empty = (ast.Dict, ast.List, ast.Set)
        found, cached = set(), set()
        for f in sorted(_paths.SRC.rglob("*.py")):
            mod = ".".join(f.relative_to(_paths.SRC).with_suffix("").parts)
            for st in ast.parse(f.read_text()).body:
                for dec in getattr(st, "decorator_list", []):
                    dec = dec.func if isinstance(dec, ast.Call) else dec
                    if getattr(dec, "attr", getattr(dec, "id", "")) in ("lru_cache", "cache"):
                        cached.add(f"{mod}.{st.name}")
                target = st.targets[0] if isinstance(st, ast.Assign) and len(st.targets) == 1 \
                    else st.target if isinstance(st, ast.AnnAssign) else None
                v = getattr(st, "value", None)
                if isinstance(target, ast.Name) and "CACHE" in target.id.upper() and (
                        (isinstance(v, empty) and not getattr(v, "keys", getattr(v, "elts", None)))
                        or (isinstance(v, ast.Constant) and v.value is None)
                        or (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                            and v.func.id in ("dict", "set", "list") and not v.args)):
                    found.add(f"{mod}.{target.id}")
        self.assertTrue(found and cached)
        self.assertEqual(sorted(found - set(ns["MEMOS"])), [])
        # TJ-3975: each cache wrapper, and only those, in CACHED (walked,
        # its entries left out); the containers outside it (skipped).
        self.assertEqual(sorted(cached ^ set(ns["CACHED"])), [])
        self.assertEqual(sorted(found & set(ns["CACHED"])), [])
        self.assertEqual(sorted(cached - set(ns["MEMOS"])), [])

    def test_the_state_check_s_noise(self):
        # TJ-3832: two runs of BASE's own import that differ fail the check,
        # each key named.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={
                "tests/test_src.py": HEADER + "A = 1\nK = os.putenv('TJ_PID_STATE', str(os.getpid()))\n",
                "tests/test_user.py": "from test_src import A\n"})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("state: test_src: two runs of BASE's own import differ, so the check "
                          "cannot compare them: TJ_PID_STATE (by hand)", out)
        many = "".join(f"K{i} = os.putenv('TJ_N{i}_STATE', str(os.getpid()))\n" for i in range(8))
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"tests/test_src.py": HEADER + "A = 1\n" + many,
                                               "tests/test_user.py": "from test_src import A\n"})
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("TJ_N5_STATE and 2 more (by hand)", out)

    def test_the_state_of_trees_by_hand(self):
        # A source that loses a name it keeps, keeps one that holds
        # something else, or fails to import.
        def tree(d, files):
            for f, text in files.items():
                (d / f).parent.mkdir(parents=True, exist_ok=True)
                (d / f).write_text(text)
        cases = [
            ("a lost name", {"tests/test_src.py": "from _support.sup import A\n",
                             "tests/_support/sup.py": "A = 1\n"},
             "state: test_src on HEAD lacks B"),
            ("a kept name changed", {"tests/test_src.py": "from _support.sup import A\nB = 3\n",
                                     "tests/_support/sup.py": "A = 1\n"},
             "state: test_src: B holds something else"),
            ("an import fails", {"tests/test_src.py": "from _support.sup import A\nB = 2\n",
                                 "tests/_support/sup.py": "A = 1\nraise ImportError('no')\n"},
             "state: an importer of test_src's moved names: HEAD's import fails"),
        ]
        for why, head_files, want in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                b, h, home = Path(td) / "b", Path(td) / "h", Path(td) / "home"
                tree(b, {"tests/test_src.py": "A = 1\nB = 2\n"})
                tree(h, {"tests/_support/__init__.py": "", **head_files})
                home.mkdir()
                found = ES.state_check(b, h, home, [("test_src", "sup", "x")], Path(td))
                self.assertTrue(any(want in x for x in found), found)

    def test_a_source_run_alone(self):
        # TJ-3845: run alone (no preload), the hermetic setup and the order
        # of the imports are compared; a gain of the setup is a note.
        herm = ("import os\nimport tempfile\n\nos.environ['HOME'] = tempfile.mkdtemp()\n"
                "os.environ['TJ_HERMETIC'] = '1'\n")
        common = {"tests/_hermetic.py": herm, "tests/_style.py": "import _hermetic  # noqa: F401\n\nX = 1\n",
                  "tests/_y.py": "import os\nimport sys\n\nif '_hermetic' not in sys.modules:\n"
                                 "    os.environ['TJ_Y_STATE'] = '1'\n",
                  "src/plug/__init__.py": "", "src/plug/cfg.py": "import os\n\nHOME = os.environ.get('HOME')\n",
                  "tests/_prod.py": "import os\n\nos.environ.setdefault('TJ_DATA', os.environ['HOME'] + '/x')\n"}
        sup = {"tests/_support/sup.py": "A = 1\n"}
        init = {"tests/_support/__init__.py": "import _hermetic  # noqa: F401\n"}
        cases = [
            ("the hermetic setup lost", "import _style  # noqa: F401\nA = 1\nB = 2\n",
             {"tests/test_src.py": "from _support.sup import A\nB = 2\n", **sup},
             "state: test_src run alone: HEAD's import no longer runs the hermetic setup"),
            ("a module reads HOME before it",
             "import _style  # noqa: F401\nfrom plug import cfg  # noqa: F401\nA = 1\nB = 2\n",
             {"tests/test_src.py": "from plug import cfg  # noqa: F401\nfrom _support.sup import A\nB = 2\n",
              **sup, **init},
             "state: test_src run alone: plug.cfg's HOME differ"),
            ("the setup gained", "A = 1\nB = 2\n",
             {"tests/test_src.py": "from _support.sup import A\nB = 2\n", **sup, **init}, None),
            # TJ-3969: then BASE's import followed by the setup is compared
            # with HEAD's, the two HOMEs as one.
            ("the setup gained, and a name it sets read", "import os\nA = 1\nB = os.environ.get('TJ_HERMETIC')\n",
             {"tests/test_src.py": "import os\nfrom _support.sup import A\nB = os.environ.get('TJ_HERMETIC')\n",
              **sup, **init}, "state: test_src run alone: B holds something else"),
            ("the setup gained, and the given HOME held", "import os\nA = 1\nB = 2\nH = os.environ['HOME']\n",
             {"tests/test_src.py": "import os\nfrom _support.sup import A\nB = 2\nH = os.environ['HOME']\n",
              **sup, **init}, None),
            ("the setup's HOME held", "import os\nimport _style  # noqa: F401\nA = 1\nB = 2\nH = os.environ['HOME']\n",
             {"tests/test_src.py": "import os\nimport _style  # noqa: F401\nfrom _support.sup import A\nB = 2\n"
                                   "H = os.environ['HOME']\n", **sup}, ""),
            ("the given HOME held", "import os\nA = 1\nB = 2\nH = os.environ['HOME']\n",
             {"tests/test_src.py": "import os\nfrom _support.sup import A\nB = 2\nH = os.environ['HOME']\n", **sup},
             ""),
            ("a variable made from the given HOME",
             "import _prod  # noqa: F401\nimport _style  # noqa: F401\nA = 1\nB = 2\n",
             {"tests/test_src.py": "import _style  # noqa: F401\nimport _prod  # noqa: F401\n"
                                   "from _support.sup import A\nB = 2\n", **sup},
             "state: test_src run alone: the environment differs: TJ_DATA"),     # (TJ-3868)
            ("an effect only without the setup", "import _y  # noqa: F401\nA = 1\nB = 2\n",
             {"tests/test_src.py": "from _support.sup import A\nB = 2\n", **sup},
             "state: test_src run alone: it no longer imports _y, and importing _y changes TJ_Y_STATE"),
        ]
        for why, base_src, head_files, want in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                b, h, home = Path(td) / "b", Path(td) / "h", Path(td) / "home"
                for d, files in ((b, {"tests/test_src.py": base_src}),
                                 (h, {"tests/_support/__init__.py": "", **head_files})):
                    for f, text in {**common, **files}.items():
                        (d / f).parent.mkdir(parents=True, exist_ok=True)
                        (d / f).write_text(text)
                home.mkdir()
                out = io.StringIO()
                with redirect_stdout(out):
                    found = ES.state_check(b, h, home, [("test_src", "sup", "x")], Path(td))
                note = "check: note: test_src run alone: HEAD's import runs the hermetic setup"
                self.assertFalse(any("two runs of BASE's own import differ" in x for x in found), found)
                if want:
                    self.assertTrue(any(want in x for x in found), found)
                elif want is None:
                    self.assertEqual(found, [])
                    self.assertIn(note, out.getvalue())
                else:
                    self.assertEqual(found, [])
                    self.assertNotIn(note, out.getvalue())

    def test_the_snapshot_walk(self):
        # TJ-3830, TJ-3838: what the walk tells apart, and what it does not.
        import collections
        import functools
        import itertools
        import decimal
        import random
        import threading
        import types
        import typing
        ns: dict = {}
        exec(ES._SNAP, ns)
        snap = ns["snap"]

        def mk(x):
            def f():
                return x
            return f

        def dflt(a):
            def f(v=a, *, k=a):
                return v
            return f

        def posd(a):
            def f(v=a):
                return v
            return f

        def kwd(a):
            def f(*, k=a):
                return k
            return f

        def attr(a):
            f = mk(0)
            f.x = a
            return f

        class Obj:
            def m(self):
                return 1

        def obj(a):
            o = Obj()
            o.a = a
            return o

        held = threading.Lock()
        held.acquire()

        def done():
            x = gen(0)
            x.close()
            return x

        def yf(n):      # the same frame, its delegate advanced (TJ-3865)
            def g():
                yield from itertools.count(1)
            x = g()
            for _ in range(n):
                next(x)
            return x

        def two(n):     # the same locals, another place
            def g():
                yield 1
                yield 2
                yield 3
            x = g()
            for _ in range(n):
                next(x)
            return x

        class P:
            __slots__ = ("a",)

        def slot(a):
            o = P()
            o.a = a
            return o

        class L(list):
            pass

        def lst(a):
            x = L()
            x.tag = a
            return x

        def fake(a):
            m = types.ModuleType("fake_m")
            m.a = a
            return m

        def klass(a):
            return type("C", (), {"__eq__": mk(a), "a": 1})

        def own(a):     # a class that is its module's own name: compared by it
            name = f"tj_own_mod_{a}"
            mod = types.ModuleType(name)
            c = type("C", (), {"__eq__": mk(a), "a": a, "__module__": name})
            mod.C = c
            sys.modules[name] = mod
            self.addCleanup(sys.modules.pop, name, None)
            return c

        def ctx(flag):
            c = decimal.Context(prec=12)
            if flag:
                c.create_decimal("1.0000000000001")     # sets Inexact and Rounded
            return c

        def gen(n):
            def g():
                i = 0
                while True:
                    i += 1
                    yield i
            x = g()
            for _ in range(n):
                next(x)
            return x

        def prop(a):
            return property(mk(a))

        def cm(a):
            return classmethod(mk(a))

        def lru(n, wrap=functools.lru_cache(maxsize=None)):     # TJ-3965: its entries
            f = wrap(lambda x: x)
            for i in range(n):
                f(i)
            return f

        def tagged(n):
            w = functools.cache(mk(1))
            w.tag = n
            return w

        def meth(n):
            class C:
                @functools.lru_cache(maxsize=None)
                def m(self):
                    return 1
            for _ in range(n):
                C().m()
            return C
        differ = [
            ("a closure", mk(1), mk(2)), ("a default", dflt(1), dflt(2)),
            ("a positional default", posd(1), posd(2)), ("a keyword-only default", kwd(1), kwd(2)),
            ("a function's attribute", attr(1), attr(2)), ("an object", obj(1), obj(2)),
            ("a slot", slot(1), slot(2)), ("a bound method", obj(1).m, obj(2).m),
            ("a partial", functools.partial(mk(0), 1), functools.partial(mk(0), 2)),
            ("a set", {1}, {2}), ("a dict's value", {1: 2}, {1: 3}), ("a dict's key", {1: 2}, {3: 2}),
            ("a list's attribute", lst(1), lst(2)),
            ("a deque", collections.deque([1]), collections.deque([2])),
            ("a defaultdict's factory", collections.defaultdict(list), collections.defaultdict(set)),
            ("a fake module", fake(1), fake(2)), ("a class's dunder", klass(1), klass(2)),
            ("a property", prop(1), prop(2)), ("a classmethod", cm(1), cm(2)),
            ("a Random's state", random.Random(1), random.Random(2)),
            ("a count", itertools.count(1), itertools.count(2)),
            ("a deque of objects", collections.deque([obj(1)]), collections.deque([obj(2)])),
            ("a lock held", held, threading.Lock()),
            ("a nested class", [klass(1)], [klass(2)]),
            ("a generator advanced", gen(1), gen(2)), ("a generator done", gen(0), done()),
            ("a generator's place", two(1), two(2)),
            ("a generator's delegate", yf(1), yf(2)),
            ("a decimal context's traps", decimal.Context(traps=[decimal.Inexact]), decimal.Context(traps=[])),
            ("a decimal context's settings", decimal.Context(prec=5), decimal.Context(prec=6)),
            ("an annotation", typing.List[int], typing.List[str]), ("a generic", list[int], list[str]),
            ("an lru_cache's entries", lru(0), lru(1)), ("a cache's entries", lru(1, functools.cache), lru(2, functools.cache)),
            ("a method's cache", meth(0), meth(1)), ("what a cache wraps", functools.cache(mk(1)), functools.cache(mk(2))),
            # TJ-4013 leaves out only where a wrapper was defined.
            ("a cache's size", functools.lru_cache(maxsize=1)(mk(1)), functools.lru_cache(maxsize=2)(mk(1))),
            ("a cache wrapper's attribute", tagged(1), tagged(2)),
        ]
        for why, a, b in differ:
            with self.subTest(why):
                self.assertNotEqual(snap(a), snap(b))
        self.assertEqual(snap(os.environb), snap(os.environ))      # the live one, by name
        # An abstract class: its registry, not its caches (an isinstance call fills them).
        import abc as abc_

        def abstract(reg):
            class Base(abc_.ABC):
                pass
            if reg:
                Base.register(int)
            return Base
        seen = abstract(False)
        before = snap(seen)
        isinstance(1.5, seen)
        self.assertEqual(snap(seen), before)
        self.assertFalse(ns["walk"]["cut"])
        self.assertNotEqual(snap(abstract(True)), snap(abstract(False)))
        # TJ-3977: a cache of a function without parameters, by its one
        # entry's value; one of a function with *args is not called (a call
        # it misses would run the function).

        def one(v, size=None):
            g = {"G": {"v": v}}
            exec("import functools\n\n\n" f"@functools.lru_cache(maxsize={size})\ndef f():\n    return G['v']\n", g)
            g["f"]()
            g["G"]["v"] = "same"
            return g["f"]
        self.assertNotEqual(snap(one("a")), snap(one("b")))
        self.assertNotEqual(snap(one("a", 8)), snap(one("b", 8)))      # a bounded one too
        calls = []

        @functools.lru_cache(maxsize=None)
        def star(*a):
            calls.append(a)
            return a
        star(1)
        snap(star)
        self.assertEqual((calls, star.cache_info().currsize), ([(1,)], 1))
        # TJ-3990: nor one of a function with a default, a keyword-only
        # parameter or **k, nor an empty one (one entry only); TJ-3992: by
        # the function the wrapper calls, not a `__wrapped__` put in its place.
        ran = []

        def pos(x=0):
            ran.append(("pos", x))

        def kw(*, x=0):
            ran.append(("kw", x))

        def kws(**k):
            ran.append(("kws", k))

        def none():
            ran.append(("none",))
        caches = [functools.lru_cache(maxsize=None)(f) for f in (pos, kw, kws)]
        caches[0](1)
        caches[1](x=1)
        caches[2](x=1)
        empty = functools.lru_cache(maxsize=None)(none)
        for size in (None, 8):
            caches.append(functools.lru_cache(maxsize=size)(pos))
            caches[-1](2)
            functools.update_wrapper(caches[-1], mk(1))     # a `__wrapped__` of no parameters
        caches.append(functools.lru_cache(maxsize=None)(pos))
        caches[-1](3)
        caches[-1].__wrapped__ = mk(1)
        before = list(ran)
        for c in [*caches, empty]:
            snap(c)
        self.assertEqual((ran, empty.cache_info().currsize), (before, 0))
        # TJ-4005: one whose function the walk cannot find (no
        # cache_parameters: its __dict__ cleared, the C type built by hand;
        # or ones not its own, so the probe's layout is not its) is cut
        # short, not called; one of a function with a parameter, or an
        # empty one, is not cut.
        unseen = [functools.lru_cache(maxsize=None)(none)] + [type(empty)(none, None, False, functools._CacheInfo)
                                                              for _ in range(2)]
        unseen[0].__dict__.clear()
        unseen[2].cache_parameters = lambda: {"maxsize": 8, "typed": False}
        for c in unseen:
            c()
        before = list(ran)
        for c in unseen:
            snap(c)
            self.assertTrue(ns["walk"]["cut"])
        for c in (caches[0], empty):
            snap(c)
            self.assertFalse(ns["walk"]["cut"])
        self.assertEqual(ran, before)
        # TJ-3875: a value seen by its type alone is a walk cut short.
        import queue
        for why, v, cut in (("a SimpleQueue", queue.SimpleQueue(), True), ("a count", itertools.count(3), False),
                            ("an object()", object(), False)):
            with self.subTest(why):
                snap(v)
                self.assertEqual(ns["walk"]["cut"], cut)
        # TJ-3906: an outside object with attributes is walked through them,
        # by its type alone or not; one of a repository class whose outside
        # base its reduce does not read cuts the walk, one it reads does not.
        a, b = functools.cached_property(mk(1)), functools.cached_property(mk(2))
        self.assertNotEqual(snap(a), snap(b))
        self.assertFalse(ns["walk"]["cut"])

        class Q(queue.SimpleQueue):
            pass

        class B(bytearray):
            pass
        ns["real"] = os.path.realpath(_paths.TESTS)     # this module's classes are the repository's
        try:
            snap(Q())
            self.assertTrue(ns["walk"]["cut"])
            self.assertNotEqual(snap(B(b"a")), snap(B(b"b")))
            self.assertFalse(ns["walk"]["cut"])
        finally:
            ns["real"] = ns["root"]
        # TJ-3910: copyreg's record of a class's slot names, written when one
        # of its objects is first reduced, is no state of the class's.
        import copyreg

        class Slotted:
            __slots__ = ("a",)
        before = snap([Slotted])
        copyreg._slotnames(Slotted)
        self.assertIn("__slotnames__", vars(Slotted))
        self.assertEqual(snap([Slotted]), before)
        same = [("two objects alike", obj(1), obj(1)), ("two objects()", object(), object()),
                ("two locks", threading.Lock(), threading.Lock()),
                ("a cycle", (lambda x: x.append(x) or x)([]), (lambda x: x.append(x) or x)([])),
                ("a module's own class by its name", [own(1)], [own(2)]),
                ("a decimal context's flags", [ctx(True)], [ctx(False)])]
        for why, a, b in same:
            with self.subTest(why):
                self.assertEqual(snap(a), snap(b))
        # Cut short: past DEPTH, past BUDGET; not short of them.
        nest = []
        for _ in range(ns["DEPTH"] + 2):
            nest = [nest]
        snap(nest)
        self.assertTrue(ns["walk"]["cut"])
        snap(list(range(ns["BUDGET"] + 1)))
        self.assertTrue(ns["walk"]["cut"])
        snap([[1]] * 10)
        self.assertFalse(ns["walk"]["cut"])
        snap((lambda x: x.append(x) or x)([]))      # a cycle is no cut
        self.assertFalse(ns["walk"]["cut"])
        ann = int
        for _ in range(ns["DEPTH"] + 2):
            ann = typing.Optional[typing.Tuple[ann, str]]
        snap({"x": ann})
        self.assertFalse(ns["walk"]["cut"])
        # The hermetic setup's values by their name; the root, and addresses.
        ns["SUBS"], ns["root"], ns["real"] = [("/tmp/xyz-home", "<HOME>")], "/r1", "/r1"
        one = (snap("/tmp/xyz-home/a"), snap("/r1/tests"), snap(Obj))
        ns["SUBS"], ns["root"], ns["real"] = [("/tmp/abc-home", "<HOME>")], "/r2", "/r2"
        self.assertEqual(one, (snap("/tmp/abc-home/a"), snap("/r2/tests"), snap(Obj)))

    def test_an_allowed_doc_is_listed_and_only_a_doc(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, _append("docs/notes.md", "more\n"))
            rc, out = _check(r, base, head, ["docs/notes.md"])
            self.assertEqual(rc, 0, out)
            self.assertIn("by hand: ['docs/notes.md']", out)
        # Each condition alone (TJ-3798): a .md, outside tests/, there in HEAD.
        for why, edit, allow in (
                ("not a doc", _append("tests/test_user.py", "X = 1\n"), "tests/test_user.py"),
                ("not .md", _append("docs/notes.txt", "more\n"), "docs/notes.txt"),
                ("below tests/", _append("tests/notes.md", "more\n"), "tests/notes.md"),
                ("deleted", lambda r: (r / "docs/notes.md").unlink(), "docs/notes.md")):
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r, edit)
                rc, out = _check(r, base, head, [allow])
                self.assertEqual(rc, 1, out)
                self.assertIn(f"{allow} is not what", out)

    def test_a_tree_git_would_not_make_is_refused(self):
        # A root tree written as raw bytes: an entry twice (TJ-3827), or one
        # named .git; each is refused, never read past.
        def raw_tree(r, head, extra):
            raw = subprocess.run(["git", "cat-file", "tree", head], cwd=r, check=True,
                                 capture_output=True).stdout
            ents, i = [], 0
            while i < len(raw):
                sp, nul = raw.index(b" ", i), raw.index(b"\0", i)
                ents.append((raw[i:sp], raw[sp + 1:nul], raw[nul + 1:nul + 21]))
                i = nul + 21
            ents += extra(dict((n, (m, o)) for m, n, o in ents))
            ents.sort(key=lambda e: e[1] + (b"/" if e[0] == b"40000" else b""))
            data = b"".join(m + b" " + n + b"\0" + o for m, n, o in ents)
            return subprocess.run(["git", "hash-object", "-t", "tree", "--literally", "-w",
                                   "--stdin"], cwd=r, input=data, check=True,
                                  capture_output=True).stdout.decode().strip()
        cases = [
            ("twice", lambda d: [(b"40000", b"docs", d[b"docs"][1])], "twice in the tree"),
            (".git", lambda d: [(b"40000", b".git", d[b"docs"][1])], "not a path inside the tree"),
        ]
        for why, extra, want in cases:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                base, head = _check_repo(r)
                rc, out = _check(r, base, raw_tree(r, head, extra))
                self.assertEqual(rc, 1, out)
                self.assertIn(want, out)

    def test_a_submodule_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r)
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "idx")}
            subprocess.run(["git", "read-tree", head], cwd=r, env=env, check=True)
            subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                            f"160000,{base},tests/sub"], cwd=r, env=env, check=True)
            head2 = subprocess.run(["git", "write-tree"], cwd=r, env=env, check=True,
                                   capture_output=True, text=True).stdout.strip()
            rc, out = _check(r, base, head2)
            self.assertEqual(rc, 1, out)
            self.assertIn("tests/sub: a symlink or a submodule (mode 160000)", out)

    def test_a_second_batch_on_a_base_with_support_modules(self):
        # The table is HEAD's new support modules only (TJ-3798).
        two = SRC.replace("_row", "_row2").replace("TestSrc", "TestSrc2")
        files = {"tests/test_src2.py": two,
                 "tests/test_user2.py": "from test_src2 import _row2\n"}
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r, files, tools=True)
            (r / "tests/test_test_layout.py").write_text(LAYOUT.replace("= 1", "= 2"))
            _git(r, "init", "-q")
            _run(r)
            _git(r, "add", "-A")
            base = _git(r, "write-tree").strip()
            _run(r, [("test_src2", "sup2", "the second rows")])
            _git(r, "add", "-A")
            head = _git(r, "write-tree").strip()
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 0, out)
            self.assertIn("check: 1 source(s): test_src2 -> sup2", out)

    def test_bases_copy_is_what_runs(self):
        # The checkout's copy hands the check to BASE's (TJ-3798).
        def marked(r):
            f = r / "scripts/extract_support.py"
            f.write_text(f.read_text().replace("def main(argv=None) -> int:\n",
                                               "def main(argv=None) -> int:\n"
                                               "    print('BASE COPY RAN')\n", 1))
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, base_edit=marked)
            run = subprocess.run([sys.executable, "-I", "-S",
                                  str(_paths.REPO / "scripts/extract_support.py"), "--check",
                                  base, head, "--trees", "--no-ids", f"--repo={r}"],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("BASE COPY RAN", run.stdout)

    def test_the_support_package_gets_the_hermetic_setup(self):
        # TJ-3829: a module run alone that imports a support module gets
        # the synthetic HOME, as it did through the source's helpers.
        for files, want in (({"tests/_hermetic/__init__.py": ""}, True),
                            ({"tests/_hermetic.py": ""}, True), ({}, False)):
            with self.subTest(files=sorted(files)), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _repo(r, files)
                _run(r)
                init = (r / "tests/_support/__init__.py").read_text()
                self.assertTrue(init.startswith(ES.INIT_DOC))
                self.assertEqual("\nimport _hermetic  # noqa: F401" in init, want)

    def test_a_failing_check_architecture(self):
        # TJ-3838: BASE's check_architecture failing on the work tree fails
        # the check, named.
        fail = "import sys\nprint('the stand-in fails')\nsys.exit(3)\n"
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, base_edit=lambda r: (
                r / "scripts/check_architecture.py").write_text(fail))
            rc, out = _check(r, base, head)
            self.assertEqual(rc, 1, out)
            self.assertIn("baseline: BASE's check_architecture fails on the work tree", out)
            self.assertIn("the stand-in fails", out)

    def test_a_package_s_tests_load_its_src(self):
        # TJ-3824, TJ-3837: a package's tests import its src/, which the
        # ids and the state runs put on the path.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={
                "packages/plug/src/plugmod.py": "X = 1\n",
                "packages/plug/tests/test_p.py": "import unittest\n\nimport plugmod\n\n\n"
                                                 "class TestP(unittest.TestCase):\n"
                                                 "    def test_p(self):\n        self.assertEqual(plugmod.X, 1)\n"})
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = ES.check(r, base, head, [], False)
            self.assertEqual(rc, 0, buf.getvalue())
            self.assertIn("packages/plug/tests 1 in HEAD, 1 in BASE", buf.getvalue())
            env = ES._child_env(r, Path(td))
            self.assertIn(str(r / "packages/plug/src"), env["PYTHONPATH"].split(os.pathsep))

    def test_the_ids(self):
        # A module that fails to load, and ids that differ, fail (TJ-3798).
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"tests/test_broken.py": "import no_such_module_x\n",
                                               "tests/_hermetic.py": ""})
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                self.assertEqual(ES.check(r, base, head, [], False), 1)
            self.assertIn("ids: base fails to load a module in tests", out.getvalue())
        fake = ("import json, os, sys\n"
                "ids = ['x.T.a'] + (['x.T.b'] if os.sep + 'head' + os.sep in "
                "os.getcwd() + os.sep else [])\n"
                "json.dump({'ids': ids, 'errors': []}, open(sys.argv[1], 'w'))\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"packages/plug/tests/test_p.py": "X = 1\n"})
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out), mock.patch.object(ES, "_IDS", fake):
                self.assertEqual(ES.check(r, base, head, [], False), 1)
            # TJ-3824: each package's test tree too.
            self.assertIn("ids: tests: 0 of BASE's missing, 1 new", out.getvalue())
            self.assertIn("ids: packages/plug/tests: 0 of BASE's missing, 1 new", out.getvalue())

    def test_a_fixture_base_s_markers_and_skip(self):
        # TJ-3946: the tool moves a fixture base and a mixin as they are, and
        # the check's ids stage passes; a move that loses a class-level
        # marker or skip, or a test's own marker, fails it, the ids the same.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base, head = _check_repo(r, files={"tests/test_src.py": FIXTURE_SRC,
                                               "tests/test_user.py": FIXTURE_USER})
            self.assertIn("class _Base(unittest.TestCase)", (r / "tests/_support/sup.py").read_text())
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                rc = ES.check(r, base, head, [], False)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertIn("check: ids: tests 5 in HEAD, 5 in BASE", out.getvalue())
        sup = "import os\nimport unittest\n\n\n" + RULE + BASE_CLASSES
        lost = [("as it was", sup, []),
                ("the base's marker", sup.replace("@_rule\n" "@unittest", "@unittest"),
                 ["test_src.TestSrc.test_a", "test_src.TestSrc.test_m", "test_user.TestUser.test_b",
                  "test_user.TestUser.test_m"]),
                ("the base's skip", sup.replace("@unittest.skipIf(os.sep, 'tj skip')\n", ""),
                 ["test_src.TestSrc.test_a", "test_src.TestSrc.test_m", "test_user.TestUser.test_b",
                  "test_user.TestUser.test_m"]),
                ("the skip's reason", sup.replace("'tj skip'", "'tj other'"),
                 ["test_src.TestSrc.test_a", "test_src.TestSrc.test_m", "test_user.TestUser.test_b",
                  "test_user.TestUser.test_m"]),
                ("a test's marker", sup.replace("    @_rule\n    def test_m", "    def test_m"),
                 ["test_src.TestSrc.test_m", "test_user.TestMixed.test_m", "test_user.TestUser.test_m"]),
                # TJ-4003: the marker's rule id; a skip without a reason.
                ("the markers' rule id (the base's and the mixin's)", sup.replace("'CA-X-01'", "'CA-X-02'"),
                 ["test_src.TestSrc.test_a", "test_src.TestSrc.test_m", "test_user.TestMixed.test_m",
                  "test_user.TestUser.test_b", "test_user.TestUser.test_m"]),
                ("a skip without a reason", sup.replace("@unittest.skipIf(os.sep, 'tj skip')\n", ""),
                 ["test_src.TestSrc.test_a", "test_src.TestSrc.test_m", "test_user.TestUser.test_b",
                  "test_user.TestUser.test_m"], BASE_CLASSES.replace("'tj skip'", "''"))]
        for why, text, differ, *classes in lost:
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                b, h = _ids_trees(Path(td), text, *classes)
                with redirect_stdout(io.StringIO()) as out:
                    got = ES.ids_check(b, h, Path(td) / "home", Path(td))
                self.assertIn("check: ids: tests 5 in HEAD, 5 in BASE", out.getvalue())
                self.assertEqual(got, [f"ids: tests: {len(differ)} test(s) whose markers or skip differ "
                                       f"(`__tax_rules__`, `__unittest_skip__`): {differ}"] if differ else [])

    def test_the_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _repo(r)
            _git(r, "init", "-q")
            _git(r, "add", "-A")
            tree = _git(r, "write-tree").strip()
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(ES.main(["--check", tree, tree, f"--repo={r}"]), 2)
                self.assertEqual(ES.main(["--check", tree, tree, "--trees", f"--repo={r}"]), 2)
                self.assertEqual(ES.main(["--check", "--dry-run", f"--repo={r}"]), 2)
                self.assertEqual(ES.main(["a", "b"]), 2)
            self.assertIn("are commits", err.getvalue())
            self.assertIn("BASE lacks one of scripts/extract_support.py", err.getvalue())


class TestTheSupportModules(unittest.TestCase):
    def test_each_is_the_tools(self):
        # A support module is written by the tool (its header names its
        # source), so a batch's --check can read the table from it.
        d = _paths.TESTS / "_support"
        for p in sorted(d.glob("*.py")) if d.is_dir() else []:
            if p.name == "__init__.py":
                continue
            with self.subTest(p.name):
                lines = (ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")),
                                           clean=False) or "").splitlines()
                self.assertTrue(lines and lines[0].startswith(ES.HEADLINE), p.name)
                self.assertEqual(sum(ln.startswith(ES.MARK) for ln in lines), 1, p.name)


if __name__ == "__main__":
    unittest.main()

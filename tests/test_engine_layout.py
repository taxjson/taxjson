"""The engine package's layers (PLAN.md step 10; scripts/move_defs.py's layers stage).

Canadian and US law never mix: no module of engine/ca imports engine/us or
the reverse; a shared module of engine/ imports neither; only
engine/by_country.py (`get_tax_rules`) imports both. Before the first batch
the package does not exist and nothing is checked; each batch's modules are
then held to it."""
import ast
import subprocess
import sys
import unittest

import _paths

ENGINE = _paths.SRC / "taxjson" / "engine"
JOINS = {"taxjson.engine.by_country"}


def _modules():
    """{dotted name: the engine modules it imports} for each file of the package."""
    out = {}
    for p in sorted(ENGINE.rglob("*.py")) if ENGINE.is_dir() else []:
        parts = p.relative_to(_paths.SRC).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        got = set()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.level:
                got.add("relative")
            elif isinstance(n, ast.ImportFrom) and n.module:
                # `from taxjson.engine import us` imports the subpackage.
                got.add(n.module)
                got |= {f"{n.module}.{a.name}" for a in n.names}
            elif isinstance(n, ast.Import):
                got |= {a.name for a in n.names}
        out[name] = {g for g in got if g == "relative" or g.startswith("taxjson.engine")}
    return out


def _side(name):
    for side in ("ca", "us"):
        if name == f"taxjson.engine.{side}" or name.startswith(f"taxjson.engine.{side}."):
            return side
    return None


class TestTheEngineLayers(unittest.TestCase):
    def test_no_module_crosses_the_law_boundary(self):
        bad = []
        for name, imports in _modules().items():
            sides = {_side(i) for i in imports} - {None}
            if "relative" in imports:
                bad.append(f"{name}: a relative import")
            if name in JOINS:
                continue
            mine = _side(name)
            if mine is None and sides:
                bad.append(f"{name} (shared) imports {', '.join(sorted(sides))}")
            elif mine and sides - {mine}:
                bad.append(f"{name} imports {', '.join(sorted(sides - {mine}))}")
        self.assertEqual(bad, [])

    def test_only_by_country_imports_both(self):
        both = sorted(n for n, imports in _modules().items()
                      if {_side(i) for i in imports} >= {"ca", "us"})
        self.assertLessEqual(set(both), JOINS)

    def test_the_rule_reads_a_crossing(self):
        self.assertEqual(_side("taxjson.engine.ca.walk"), "ca")
        self.assertEqual(_side("taxjson.engine.us"), "us")
        self.assertIsNone(_side("taxjson.engine.rows"))
        self.assertIsNone(_side("taxjson.engine.cash"))



def _reads_lib_country(source):
    """The lines of `source` that import taxjson.lib.country (`import`,
    `from ... import`, `from taxjson.lib import country`, at any depth),
    and every relative import, which this check cannot resolve."""
    out = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import) and any(a.name == "taxjson.lib.country" for a in n.names):
            out.append(n.lineno)
        elif isinstance(n, ast.ImportFrom) and (n.level or (
                n.module == "taxjson.lib.country"
                or (n.module == "taxjson.lib" and any(a.name == "country" for a in n.names)))):
            out.append(n.lineno)
    return out


def _imports_leaf(source):
    """Whether `source` imports from taxjson.rules.country."""
    return any(isinstance(n, ast.ImportFrom) and n.module == "taxjson.rules.country"
               for n in ast.walk(ast.parse(source)))


# The leaf's readers B7 made (TJ-4063): a reader renamed or gone fails here.
LEAF_READERS = ("characterize/foreign_roc.py", "parsers/settings.py", "parsers/conformance.py")

# What importing the engine must not load: the project layer, and the
# modules that load it.
NOT_LOADED = ("taxjson.project", "taxjson.lib.project_layout", "taxjson.lib.country")

_LOADED = """
import importlib, pkgutil, sys
sys.path.insert(0, sys.argv[1])
import taxjson.engine as E
# -I drops PYTHONPATH, and an editable install's .pth names the dev clone:
# the tree under test must be the one imported.
assert E.__file__.startswith(sys.argv[1]), E.__file__
for m in pkgutil.walk_packages(E.__path__, "taxjson.engine."):
    importlib.import_module(m.name)
print(len(sys.modules))
for name in sorted(sys.modules):
    print(name)
"""


class TestTheCountryLeaf(unittest.TestCase):
    """Step 10's done-when: the parse and characterize read a project's
    country through taxjson.rules.country, the leaf, never lib/country.py
    (which loads the project layer); the engine too (TJ-4068)."""

    def test_the_parse_characterize_and_the_engine_read_the_leaf(self):
        bad = []
        for pkg in ("parsers", "characterize", "engine"):
            files = sorted((_paths.SRC / "taxjson" / pkg).rglob("*.py"))
            self.assertGreater(len(files), 1, f"no modules under {pkg}/")
            for p in files:
                for line in _reads_lib_country(p.read_text(encoding="utf-8")):
                    bad.append(f"{p.relative_to(_paths.SRC)}:{line}")
        self.assertEqual(bad, [])

    def test_the_known_readers_import_the_leaf(self):
        for rel in LEAF_READERS:
            p = _paths.SRC / "taxjson" / rel
            self.assertTrue(_imports_leaf(p.read_text(encoding="utf-8")), rel)

    def test_the_rule_reads_each_form(self):
        self.assertEqual(_reads_lib_country(
            "import taxjson.lib.country\n"
            "from taxjson.lib.country import CANADA\n"
            "from taxjson.lib import country\n"
            "def f():\n    from taxjson.lib.country import USA\n"), [1, 2, 3, 5])
        self.assertEqual(_reads_lib_country("from ..lib import country\nfrom . import x\n"), [1, 2])
        self.assertTrue(_imports_leaf("def f():\n    from taxjson.rules.country import USA\n"))
        self.assertFalse(_imports_leaf("from taxjson.lib.country import USA\n"))
        self.assertEqual(_reads_lib_country(
            "from taxjson.rules.country import CANADA\nfrom taxjson.lib import money\n"), [])


class TestTheEngineLoadsNoProject(unittest.TestCase):
    """TJ-4069: the engine reads no project setting, so importing it, in a
    fresh interpreter, loads no module of taxjson.project (the loss
    overrides' project reader imports it when it runs), nor lib/country.py
    or the shim."""

    def test_importing_every_engine_module_loads_no_project_module(self):
        r = subprocess.run([sys.executable, "-I", "-c", _LOADED, str(_paths.SRC)],
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = r.stdout.split()[1:]
        self.assertIn("taxjson.engine.by_country", loaded)
        self.assertIn("taxjson.rules.country", loaded)
        bad = [m for m in loaded if any(m == n or m.startswith(n + ".") for n in NOT_LOADED)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()

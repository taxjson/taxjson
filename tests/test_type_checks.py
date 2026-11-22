"""The types stage (PLAN step 8): scripts/ci.sh and CI's lint job run the
same mypy line, strict over the packages pyproject.toml's [tool.mypy]
names, with the mypy of the dev lock.

Step 10 added the engine with a ratchet: the modules strict still finds
errors in are silenced by one overrides table, and that list only
shrinks (T1 to T4 type them, each commit proven annotation-only by
scripts/check_annotations_only.py). Those proofs assume nothing in src
reads an annotation at runtime; a test here holds that.

Nothing here runs mypy: the stage does (a missing mypy fails it).
"""
import ast
import re
import unittest
from pathlib import Path

import tomllib
import _paths

REPO = _paths.REPO
CI_SH = REPO / "scripts" / "ci.sh"
TESTS_YML = REPO / ".github" / "workflows" / "tests.yml"
PYPROJECT = REPO / "pyproject.toml"
TYPED = {"taxjson.model", "taxjson.rules", "taxjson.characterize", "taxjson.engine"}
# The engine modules mypy strict found errors in when the engine joined the
# typed packages (step 10 T0: 821 errors in 29 files). The overrides list is
# a subset of this one; a module leaves it once typed, never to return.
UNTYPED_AT_T0 = frozenset({
    "taxjson.engine.assign",
    "taxjson.engine.ca.denial",
    "taxjson.engine.ca.fold",
    "taxjson.engine.ca.holders",
    "taxjson.engine.ca.indexes",
    "taxjson.engine.ca.own_moves",
    "taxjson.engine.ca.pools",
    "taxjson.engine.ca.router",
    "taxjson.engine.ca.run_helpers",
    "taxjson.engine.ca.solve",
    "taxjson.engine.ca.taxrules",
    "taxjson.engine.ca.walk",
    "taxjson.engine.consts",
    "taxjson.engine.journal",
    "taxjson.engine.premium_ledger",
    "taxjson.engine.replacement",
    "taxjson.engine.rows",
    "taxjson.engine.splits",
    "taxjson.engine.taxrules",
    "taxjson.engine.us.lot_store",
    "taxjson.engine.us.lots",
    "taxjson.engine.us.option_scan",
    "taxjson.engine.us.prepare",
    "taxjson.engine.us.run",
    "taxjson.engine.us.taxrules",
    "taxjson.engine.us.terms",
    "taxjson.engine.us.trades",
    "taxjson.engine.us.walk",
    "taxjson.engine.us.wash",
})
SRC = REPO / "src" / "taxjson"
# What reads an annotation while taxjson runs (a future import or a typing
# commit would change its answer), and the readers allowed: none since
# model/events.py, whose `get_args` read its event union, was retired
# (PLAN step 14).
ANNOTATION_READERS = frozenset({
    "__annotations__", "__annotate__", "get_type_hints", "get_annotations",
    "singledispatch", "singledispatchmethod", "annotation", "return_annotation",
    "get_origin", "get_args", "TypeAdapter", "validate_call", "getfullargspec",
    "formatannotation"})
READERS_ALLOWED: frozenset = frozenset()
# [tool.mypy]'s keys: one more (exclude, disable_error_code, ignore_errors...)
# could silence a typed module the ratchet no longer lists.
MYPY_KEYS = {"python_version", "mypy_path", "packages", "strict", "follow_imports",
             "no_site_packages", "overrides"}
# An inline config comment, or a bare `# type: ignore` before a module's first
# statement (mypy then skips the whole module), silences it from inside.
INLINE_CONFIG = re.compile(r"^\s*#\s*mypy:", re.M)
WHOLE_FILE_IGNORE = re.compile(r"#\s*type:\s*ignore(?!\[)")


def _project():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestTheTypesStage(unittest.TestCase):

    def test_the_config_is_strict_over_the_typed_packages(self):
        cfg = _project()["tool"]["mypy"]
        self.assertIs(cfg["strict"], True)
        self.assertLessEqual(TYPED, set(cfg["packages"]))
        for pkg in cfg["packages"]:
            # TJ-3507: the config file's folder, not the working directory.
            root = cfg["mypy_path"].replace("$MYPY_CONFIG_FILE_DIR", str(REPO))
            path = Path(root) / Path(*pkg.split("."))
            self.assertTrue((path / "__init__.py").is_file(), pkg)
        # The source tree, as the floor reads it, in any venv.
        floor = re.fullmatch(r">=(\d+\.\d+)", _project()["project"]["requires-python"])
        self.assertEqual(cfg["python_version"], floor.group(1))
        self.assertIs(cfg["no_site_packages"], True)
        self.assertEqual(cfg["follow_imports"], "silent")

    def test_the_gate_and_ci_run_the_same_line(self):
        # No arguments: the config decides what is checked, for both.
        gate = re.findall(r'^\s*stage types "\$PY" -m mypy(.*)$',
                          CI_SH.read_text(encoding="utf-8"), re.M)
        ci = re.findall(r"^\s*- run: python -m mypy(.*)$",
                        TESTS_YML.read_text(encoding="utf-8"), re.M)
        self.assertEqual(gate, [""])
        self.assertEqual(ci, [""])

    def test_only_the_ratchet_silences_errors_and_it_only_shrinks(self):
        cfg = _project()["tool"]["mypy"]
        tables = cfg.get("overrides", [])
        self.assertLessEqual(len(tables), 1, "one overrides table: the ratchet")
        for table in tables:
            self.assertEqual(set(table), {"module", "ignore_errors"}, table)
            self.assertIs(table["ignore_errors"], True)
            listed = table["module"]
            self.assertIsInstance(listed, list)
            self.assertEqual(listed, sorted(listed), "sorted")
            self.assertEqual(len(listed), len(set(listed)), "no repeats")
            grown = sorted(set(listed) - UNTYPED_AT_T0)
            self.assertEqual(grown, [], "a typed module back in the ratchet")
            for mod in listed:
                rel = Path(*mod.split("."))
                here = (REPO / "src" / rel).with_suffix(".py").is_file() or \
                    (REPO / "src" / rel / "__init__.py").is_file()
                self.assertTrue(here, f"{mod}: no such module")

    def test_no_typed_module_is_silenced_another_way(self):
        cfg = _project()["tool"]["mypy"]
        self.assertEqual(sorted(set(cfg) - MYPY_KEYS), [],
                         "a [tool.mypy] key the ratchet does not hold")
        found = []
        for pkg in sorted(cfg["packages"]):
            for path in sorted((REPO / "src" / Path(*pkg.split("."))).rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                rel = path.relative_to(REPO).as_posix()
                if INLINE_CONFIG.search(text):
                    found.append(f"{rel}: a `# mypy:` comment")
                tree = ast.parse(text)
                first = tree.body[0].lineno if tree.body else len(text.splitlines()) + 1
                head = "\n".join(text.splitlines()[:first - 1])
                if WHOLE_FILE_IGNORE.search(head):
                    found.append(f"{rel}: a `# type: ignore` before its first statement")
        self.assertEqual(found, [])

    def test_nothing_in_src_reads_an_annotation_at_runtime(self):
        found = []
        for path in sorted(SRC.rglob("*.py")):
            rel = path.relative_to(SRC).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    name = node.id
                elif isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.alias):
                    name = node.name.rsplit(".", 1)[-1]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    name = node.value      # getattr(x, "__annotations__")
                else:
                    continue
                if name in ANNOTATION_READERS and (rel, name) not in READERS_ALLOWED:
                    found.append(f"{rel}:{node.lineno}: {name}")
        self.assertEqual(found, [], "the typing proofs assume no runtime "
                         "reader; one needs its own review (and here, a reason)")

    def test_the_dev_extra_and_its_lock_hold_mypy(self):
        dev = _project()["project"]["optional-dependencies"]["dev"]
        self.assertTrue(any(re.match(r"mypy\b", d) for d in dev), dev)
        lock = (REPO / "requirements" / "dev.txt").read_text(encoding="utf-8")
        self.assertRegex(lock, r"(?m)^mypy==\S+ \\$")


if __name__ == "__main__":
    unittest.main()

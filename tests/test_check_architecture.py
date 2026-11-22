"""scripts/check_architecture.py: the ratchet behind ci.sh's `architecture`
stage. Each check runs on a small planted tree in a folder of its own; the
real tree is the stage's job."""
import ast
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO = _paths.REPO
_spec = importlib.util.spec_from_file_location(
    "check_architecture", REPO / "scripts" / "check_architecture.py")
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)


def _plant(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


class _Tree(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)


class TestImportGraph(_Tree):
    def _graph(self, files):
        _plant(self.root / "src", {"taxjson/__init__.py": "", **files})
        return ca.import_graph(self.root / "src")

    def test_every_import_spelling_resolves(self):
        g = self._graph({
            "taxjson/lib/__init__.py": "",
            "taxjson/lib/core.py": "",
            "taxjson/lib/a.py": "import taxjson.lib.core\n",
            "taxjson/lib/b.py": "from taxjson.lib import core\n",
            "taxjson/lib/c.py": "from taxjson.lib.core import name\n",
            "taxjson/lib/d.py": "from . import core\n",
            "taxjson/lib/e.py": "from .core import name\n",
            "taxjson/lib/f.py": "from ..lib.core import name\n",
            "taxjson/lib/g.py": "def f():\n    from taxjson.lib import core\n",
            "taxjson/lib/h.py": "import json\nfrom os import path\n",
        })
        for m in "abcdefg":
            self.assertEqual(g[f"taxjson.lib.{m}"], {"taxjson.lib.core"}, m)
        self.assertEqual(g["taxjson.lib.h"], set())

    def test_an_import_under_type_checking_is_no_edge(self):
        # A module-level `if TYPE_CHECKING:` (typing's) never runs: its
        # imports are no edge, for the cycles or the layers. Its else
        # branch runs; so does a function's, or a flag of the module's own.
        tc = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from taxjson.engine import e\n"
        cases = {
            "a": (tc, False),
            "b": ("import typing\n\nif typing.TYPE_CHECKING:\n    import taxjson.engine.e\n", False),
            "c": ("from typing import TYPE_CHECKING as TC\n\nif TC:\n    pass\nelif TC:\n"
                  "    from taxjson.engine import e\n", False),
            "d": ("from taxjson.engine import e\n", True),
            "e": (tc.replace("    from", "    pass\nelse:\n    from"), True),
            "f": ("from typing import TYPE_CHECKING\n\n\ndef g():\n    if TYPE_CHECKING:\n"
                  "        from taxjson.engine import e\n", True),
            "g": ("TYPE_CHECKING = True\n\nif TYPE_CHECKING:\n    from taxjson.engine import e\n", True),
            "h": (tc + "TYPE_CHECKING = True\n", True),
            "i": (tc + "from os import *\n", True),
            "j": (tc.replace("from typing import TYPE_CHECKING", "import typing\n"
                             "from typing import TYPE_CHECKING\ntyping.TYPE_CHECKING = True"), True),
        }
        g = self._graph({"taxjson/engine/__init__.py": "", "taxjson/engine/e.py": "",
                         "taxjson/model/__init__.py": "",
                         **{f"taxjson/model/{m}.py": text for m, (text, _edge) in cases.items()}})
        for m, (_text, edge) in cases.items():
            with self.subTest(m):
                self.assertEqual(g[f"taxjson.model.{m}"], {"taxjson.engine.e"} if edge else set())
        self.assertEqual(sorted(v.split(" imports ")[0] for v in ca.layer_violations(g)),
                         [f"layers: taxjson.model.{m}" for m in "defghij"])

    def test_a_relative_import_from_a_package_init(self):
        g = self._graph({"taxjson/lib/__init__.py": "from . import core\n",
                         "taxjson/lib/core.py": ""})
        self.assertEqual(g["taxjson.lib"], {"taxjson.lib.core"})

    def test_a_string_naming_a_bin_module(self):
        _plant(self.root / "src", {
            "taxjson/__init__.py": "",
            "taxjson/bin/__init__.py": "",
            "taxjson/bin/t.py": 'x = "taxjson.bin.t"\n',
            "taxjson/lib/__init__.py": "",
            "taxjson/lib/a.py": 'argv("taxjson.bin.t")\ny = "see taxjson.bin.t for this"\n',
        })
        self.assertEqual(ca.named_modules(self.root / "src"),
                         {"taxjson.lib.a": {"taxjson.bin.t"}})

    def test_a_string_naming_a_layer_module_and_nothing_else(self):
        _plant(self.root / "src", {
            "taxjson/__init__.py": "",
            "taxjson/lib/__init__.py": "",
            "taxjson/lib/a.py": 'x = ("taxjson.cli.main", "taxjson.pipeline", "taxjson.lib.core", "taxjsonx.cli")\n',
        })
        self.assertEqual(ca.named_modules(self.root / "src"),
                         {"taxjson.lib.a": {"taxjson.cli.main", "taxjson.pipeline"}})


class TestCycles(unittest.TestCase):
    def test_components_of_two_or_more(self):
        g = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set()}
        self.assertEqual(ca.cycles(g), [["a", "b", "c"]])

    def test_a_deep_chain_does_not_hit_the_recursion_limit(self):
        n = 5000
        g = {f"m{i}": {f"m{(i + 1) % n}"} for i in range(n)}
        self.assertEqual(len(ca.cycles(g)[0]), n)

    def test_the_allow_list_is_cycle_edges_and_names_outside_lib(self):
        # lib reaching bin is a layer violation, never on it (TJ-3546).
        g = {"taxjson.lib.a": {"taxjson.lib.b", "taxjson.lib.z", "taxjson.bin.t"},
             "taxjson.lib.b": {"taxjson.lib.a"},
             "taxjson.lib.z": set(), "taxjson.bin.t": set()}
        named = {"taxjson.lib.a": {"taxjson.bin.t"}, "taxjson.cli.c": {"taxjson.bin.t"}}
        self.assertEqual(ca.allow_listed_edges(g, named=named), {
            "taxjson.lib.a -> taxjson.lib.b", "taxjson.lib.b -> taxjson.lib.a",
            "taxjson.cli.c -> taxjson.bin.t" + ca._BY_NAME})
        # Not even inside a cycle (TJ-3549).
        g = {"taxjson.lib.a": {"taxjson.bin.t"}, "taxjson.bin.t": {"taxjson.lib.a"}}
        self.assertEqual(ca.allow_listed_edges(g), {"taxjson.bin.t -> taxjson.lib.a"})

    def test_the_package_itself_counts_as_bin_and_as_lib(self):
        # Refused as lib reaching bin, either way round (never allow-listed).
        g = {"taxjson.lib": {"taxjson.bin.t"}, "taxjson.lib.a": {"taxjson.bin"},
             "taxjson.bin": set(), "taxjson.bin.t": set()}
        self.assertEqual(ca.allow_listed_edges(g), set())
        self.assertEqual(sorted(w.split(":")[1].strip() for w in ca.layer_violations(g)),
                         ["taxjson.lib imports taxjson.bin.t", "taxjson.lib.a imports taxjson.bin"])

    def test_a_named_module_is_on_the_list(self):
        # cli's or the pipeline's; lib's is a layer violation (TJ-3546).
        self.assertEqual(
            ca.allow_listed_edges({"taxjson.cli.a": set(), "taxjson.lib.b": set()}, [],
                                  {"taxjson.cli.a": {"taxjson.bin.t"}, "taxjson.lib.b": {"taxjson.bin.t"}}),
            {"taxjson.cli.a -> taxjson.bin.t (by name)"})

    def test_a_named_layer_module_is_a_layer_question_not_an_edge(self):
        self.assertEqual(ca.allow_listed_edges({"taxjson.cli.a": set()}, [],
                                               {"taxjson.cli.a": {"taxjson.pipeline.p"}}), set())


class TestLayers(unittest.TestCase):
    def test_downward_is_fine(self):
        g = {"taxjson.cli.x": {"taxjson.model.y", "taxjson.bin.t", "taxjson.lib.o"},
             "taxjson.model.y": set(), "taxjson.bin.t": set(), "taxjson.lib.o": set()}
        self.assertEqual(ca.layer_violations(g), [])

    def test_upward_is_refused(self):
        g = {"taxjson.model.y": {"taxjson.engine.e"}, "taxjson.engine.e": set()}
        self.assertIn("`model` sits below `engine`", ca.layer_violations(g)[0])

    def test_a_leaf_is_below_every_layer_and_imports_nothing(self):
        # TJ-3489: characterize (and model, parsers) may name a rule by its
        # constant; the rest of `rules` stays above them.
        g = {"taxjson.characterize.c": {"taxjson.rules.ids"},
             "taxjson.model.m": {"taxjson.rules.ids"},
             "taxjson.lib.x": {"taxjson.rules.ids"},
             "taxjson.rules.ids": set()}
        self.assertEqual(ca.layer_violations(g), [])
        g = {"taxjson.characterize.c": {"taxjson.rules.ca"}, "taxjson.rules.ca": set()}
        self.assertIn("`characterize` sits below `rules`", ca.layer_violations(g)[0])
        g = {"taxjson.rules.ids": {"taxjson.model.m"}, "taxjson.model.m": set()}
        self.assertIn("a leaf (LEAVES) imports nothing from taxjson", ca.layer_violations(g)[0])
        # Step 10 B1: the country resolver is a leaf too, which the parse
        # and characterize read.
        g = {"taxjson.parsers.p": {"taxjson.rules.country"},
             "taxjson.characterize.c": {"taxjson.rules.country"},
             "taxjson.rules.country": set()}
        self.assertEqual(ca.layer_violations(g), [])
        g = {"taxjson.rules.country": {"taxjson.model.m"}, "taxjson.model.m": set()}
        self.assertIn("a leaf (LEAVES) imports nothing from taxjson", ca.layer_violations(g)[0])
        # TJ-3499: importing the leaf runs its package's __init__.
        self.assertEqual(ca.LEAF_PARENTS, ("taxjson.rules",))
        g = {"taxjson.rules": {"taxjson.characterize.c"}, "taxjson.characterize.c": set()}
        self.assertIn("a leaf's package (LEAF_PARENTS) imports nothing from taxjson",
                      ca.layer_violations(g)[0])

    def test_below_the_pipeline_never_reaches_bin(self):
        g = {"taxjson.engine.e": {"taxjson.bin.t"}, "taxjson.bin.t": set()}
        self.assertIn("may reach taxjson.bin", ca.layer_violations(g)[0])

    def test_the_bin_package_itself_is_refused(self):
        g = {"taxjson.engine.e": {"taxjson.bin"}, "taxjson.bin": set()}
        self.assertIn("may reach taxjson.bin", ca.layer_violations(g)[0])

    def test_a_parser_imports_no_other_parser(self):
        # Step 5f: a lib.brokerages parser reaches base, schema,
        # _crypto_common, detect and the toolbox, never another parser;
        # detect routes to the generic importer.
        b = "taxjson.lib.brokerages."
        g = {b + "questrade": {b + "base", b + "rbc_direct", "taxjson.parsers.toolbox.numbers"},
             b + "detect": {b + "generic"}, b + "generic": {b + "base"},
             b + "base": set(), b + "rbc_direct": set(),
             "taxjson.parsers.toolbox.numbers": set()}
        bad = ca.layer_violations(g)
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("never another parser", bad[0])
        self.assertIn("questrade", bad[0])
        # The package root is a sibling's door too.
        g = {b + "webull": {b[:-1]}, b[:-1]: set(), b + "base": set()}
        self.assertIn("never another parser", ca.layer_violations(g)[0])

    def test_a_low_layer_may_not_name_a_bin_module(self):
        g = {"taxjson.engine.e": set(), "taxjson.cli.c": set()}
        named = {"taxjson.engine.e": {"taxjson.bin.t"}, "taxjson.cli.c": {"taxjson.bin.t"}}
        problems = ca.layer_violations(g, named)
        self.assertEqual(len(problems), 1)
        self.assertIn("taxjson.engine.e names taxjson.bin.t", problems[0])

    def test_lib_sits_below_cli_and_pipeline(self):
        g = {"taxjson.lib.o": {"taxjson.cli.c", "taxjson.pipeline.p", "taxjson.engine.e"},
             "taxjson.cli.c": set(), "taxjson.pipeline.p": set(), "taxjson.engine.e": set()}
        problems = ca.layer_violations(g)
        self.assertEqual(len(problems), 2)
        self.assertIn("`lib` sits below `cli`", problems[0])
        self.assertIn("`lib` sits below `pipeline`", problems[1])

    def test_only_the_brokerages_reach_the_parsers_from_lib(self):
        # The brokerages are the parsers moving out (step 5): they may use
        # the toolbox; the rest of lib may not (TJ-2069).
        g = {"taxjson.lib.brokerages.kraken": {"taxjson.parsers.toolbox.masking"},
             "taxjson.lib.crypto_sends": {"taxjson.parsers.toolbox.masking"},
             "taxjson.parsers.toolbox.masking": set()}
        problems = ca.layer_violations(g)
        self.assertEqual(len(problems), 1)
        self.assertIn("taxjson.lib.crypto_sends", problems[0])
        self.assertIn("only `lib.brokerages`", problems[0])

    def test_a_string_counts_like_the_import(self):
        problems = ca.layer_violations(
            {"taxjson.lib.o": set(), "taxjson.engine.e": set(), "taxjson.cli.c": set()},
            {"taxjson.lib.o": {"taxjson.cli.main"}, "taxjson.engine.e": {"taxjson.pipeline.p"},
             "taxjson.cli.c": {"taxjson.pipeline.p"}})
        self.assertEqual(len(problems), 2)
        self.assertIn("taxjson.engine.e names taxjson.pipeline.p", problems[0])
        self.assertIn("taxjson.lib.o names taxjson.cli.main", problems[1])

    def test_a_package_with_no_place_fails_and_cannot_launder_an_edge(self):
        g = {"taxjson.util.x": {"taxjson.bin.t"}, "taxjson.lib.a": {"taxjson.util.x"},
             "taxjson.engine.e": {"taxjson.util.x"}, "taxjson.bin.t": set()}
        problems = ca.layer_violations(g)
        self.assertEqual(len(problems), 1)
        self.assertIn("`util` has no place", problems[0])

    def test_a_misspelled_layer_fails(self):
        self.assertIn("`engines` has no place", ca.layer_violations({"taxjson.engines.e": set()})[0])

    def test_a_new_top_level_module_fails(self):
        self.assertIn("`helpers` has no place", ca.layer_violations({"taxjson.helpers": set()})[0])

    def test_the_real_tree_has_only_known_top_level_names(self):
        for p in (REPO / "src" / "taxjson").iterdir():
            if p.name != "__pycache__" and p.name != "__init__.py":
                self.assertIn(p.stem, ca.OLD_PACKAGES + ca.LAYERS, p)

    def test_the_old_packages_have_no_layer(self):
        g = {"taxjson.bin.t": {"taxjson.lib.o"}, "taxjson.lib.o": {"taxjson.data.d"},
             "taxjson.data.d": set()}
        self.assertEqual(ca.layer_violations(g), [])

    def test_lib_never_reaches_bin(self):
        # Step 8: neither an import nor a string naming a bin module (a child
        # process's, importlib's), so a package importing lib cannot reach bin
        # through it (TJ-1363, TJ-1366).
        g = {"taxjson.lib.o": {"taxjson.bin.t"}, "taxjson.bin.t": set()}
        (why,) = ca.layer_violations(g)
        self.assertIn("taxjson.lib.o imports taxjson.bin.t: `lib` never reaches taxjson.bin", why)
        (why,) = ca.layer_violations({"taxjson.lib.o": set()}, {"taxjson.lib.o": {"taxjson.bin._entry"}})
        self.assertIn("taxjson.lib.o names taxjson.bin._entry", why)


class TestNoqa(_Tree):
    def test_codes_and_the_blanket(self):
        _plant(self.root, {"src/a.py": (
            "import x  # noqa: F401  (a re-export)\n"
            "import y  # noqa: F401 kept for the tests\n"
            "import z  # noqa\n"
            "import w  # NOQA: E402, F401\n"
            "v = 1\n")})
        self.assertEqual(ca.noqa_counts(self.root), {
            "F401": {"src/a.py": 3}, "blanket": {"src/a.py": 1}, "E402": {"src/a.py": 1}})

    def test_a_marker_in_a_string_suppresses_nothing(self):
        _plant(self.root, {"src/a.py": (
            'X = "import x  # noqa: F401"\n'
            'Y = """\nimport y  # noqa\n"""\n'
            "# the word noqa in prose\n"
            "import z  # noqa: E402\n")})
        self.assertEqual(ca.noqa_counts(self.root), {"E402": {"src/a.py": 1}})

    def test_odd_spellings_say_what_they_mean(self):
        _plant(self.root, {"src/a.py": (
            "import a  # noqa: F401 T5 slips\n"
            "import b  # noqa: e402\n"
            "import c  # noqa : F841\n"
            "import d  #noqa:E741,E731\n"
            "import e  # type: ignore  # noqa: E711\n"
            "import f  # noqa: see above\n")})
        self.assertEqual(ca.noqa_counts(self.root), {
            "F401": {"src/a.py": 1}, "E402": {"src/a.py": 1}, "F841": {"src/a.py": 1},
            "E741": {"src/a.py": 1}, "E731": {"src/a.py": 1}, "E711": {"src/a.py": 1},
            "blanket": {"src/a.py": 1}})

    def test_a_whole_file_suppression_counts_apart(self):
        _plant(self.root, {"src/a.py": "# ruff: noqa\nv = 1\n",
                           "src/b.py": "# ruff: noqa: E741\nv = 1\n",
                           "src/c.py": "# flake8: noqa\nv = 1\n"})
        self.assertEqual(ca.noqa_counts(self.root), {
            "file:blanket": {"src/a.py": 1, "src/c.py": 1}, "file:E741": {"src/b.py": 1}})

    def test_a_range_suppression_counts_apart(self):
        _plant(self.root, {"src/a.py": ("# ruff: disable[F401, E402]\nimport os\n# ruff: enable[F401, E402]\n"
                                        "# ruff: disable\n")})
        self.assertEqual(ca.noqa_counts(self.root), {
            "range:F401": {"src/a.py": 1}, "range:E402": {"src/a.py": 1},
            "range:blanket": {"src/a.py": 1}})

    def test_a_file_that_does_not_tokenize(self):
        _plant(self.root, {"src/a.py": "import x  # noqa: F401\ny = (\n"})
        self.assertEqual(ca.noqa_counts(self.root), {"F401": {"src/a.py": 1}})


class TestRuffSettingsElsewhere(_Tree):
    def _misplaced(self, text):
        _plant(self.root, {"pyproject.toml": text})
        return ca.misplaced_ruff_settings(self.root)

    def test_dotted_keys_and_inline_tables_are_refused(self):
        for text in ('[tool]\nruff.lint.select = ["E9"]\n',
                     'tool.ruff.lint.select = ["E9"]\n',
                     '[tool]\nruff = { lint = { select = ["E9"] } }\n',
                     'tool = { ruff = { lint = { select = ["E9"] } } }\n',
                     '[tool]\n"ruff".lint.select = ["E9"]\n'):
            self.assertEqual(len(self._misplaced(text)), 1, text)
            self.assertIn("pyproject.toml", self._misplaced(text)[0])

    def test_a_table_is_fine_and_the_word_elsewhere_is_too(self):
        self.assertEqual(self._misplaced(
            '# ruff is configured below\n[project]\nname = "x"\ndev = ["ruff"]\n'
            '[tool.ruff.lint]\nselect = ["E9"]\n[tool.other]\nnote = "ruff"\n'), [])

    def test_a_quoted_or_spaced_table_is_still_read(self):
        _plant(self.root, {"pyproject.toml": '[ tool . "ruff" . lint ]\nselect = ["E9"]\n'})
        self.assertEqual(ca.misplaced_ruff_settings(self.root), [])
        self.assertEqual(ca.ruff_config(self.root),
                         {"pyproject.toml": ['[ tool . "ruff" . lint ]', 'select = ["E9"]']})


class TestPluginEdges(_Tree):
    def test_imports_and_names_of_bin_from_a_plugin(self):
        _plant(self.root, {
            "src/taxjson/__init__.py": "", "src/taxjson/bin/__init__.py": "",
            "src/taxjson/bin/t.py": "", "src/taxjson/lib/__init__.py": "",
            "src/taxjson/lib/core.py": "",
            "packages/p/src/plug/__init__.py": "from taxjson.lib import core\n",
            "packages/p/src/plug/a.py": "def f():\n    from taxjson.bin import t\n",
            "packages/p/src/plug/b.py": 'M = "taxjson.bin.t"\n',
            "packages/p/src/plug/c.py": "from . import t\n",
            "packages/p/tests/test_x.py": "from taxjson.bin import t\n",
        })
        self.assertEqual(ca.plugin_bin_edges(self.root), {
            "plug.a -> taxjson.bin.t", "plug.b -> taxjson.bin.t (by name)"})
        # Under typing's TYPE_CHECKING it never runs: no edge.
        _plant(self.root, {"packages/p/src/plug/d.py": "from typing import TYPE_CHECKING\n\n"
                           "if TYPE_CHECKING:\n    from taxjson.bin import t\n"})
        self.assertNotIn("plug.d -> taxjson.bin.t", ca.plugin_bin_edges(self.root))
        # Refused, not allow-listed (step 8: plugins read pipeline.plugin_api).
        self.assertEqual(ca.plugin_violations(self.root), [
            "layers: plug.a imports taxjson.bin.t: a plugin reads the core through "
            "taxjson.pipeline.plugin_api, never taxjson.bin (step 8)",
            "layers: plug.b names taxjson.bin.t: a plugin reads the core through "
            "taxjson.pipeline.plugin_api, never taxjson.bin (step 8)"])


class TestWhichFiles(_Tree):
    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_an_ignored_file_does_not_count_and_an_untracked_one_does(self):
        files = {".gitignore": "build/\n",
                 "src/a.py": "import x  # noqa: F401\n",
                 "src/build/lib/x.py": "import x  # noqa: F401\n",
                 "src/new.py": "import y  # noqa: F401\n"}
        _plant(self.root, files)
        self._git("init", "-q")
        self._git("add", ".gitignore", "src/a.py")
        self.assertEqual(ca.noqa_counts(self.root), {"F401": {"src/a.py": 1, "src/new.py": 1}})

    def test_a_tree_with_no_git_skips_what_ruff_skips(self):
        _plant(self.root, {"src/a.py": "import x  # noqa: F401\n",
                           "src/build/x.py": "import x  # noqa: F401\n",
                           "src/.venv/y.py": "import x  # noqa: F401\n"})
        self.assertEqual(ca.noqa_counts(self.root), {"F401": {"src/a.py": 1}})


class TestRuffItself(_Tree):
    """A runner standing in for ruff: `ruff_counts` must never read a failure
    as zero hits."""

    @staticmethod
    def _runner(rc, stdout="", stderr=""):
        def run(argv, **_kw):
            return subprocess.CompletedProcess(argv, rc, stdout, stderr)
        return run

    def test_no_ruff_is_not_zero_hits(self):
        missing = self._runner(1, "", "python: No module named ruff")
        with self.assertRaisesRegex(SystemExit, "No module named ruff"):
            ca.ruff_counts(self.root, missing)
        with self.assertRaisesRegex(SystemExit, "no ruff in"):
            ca.ruff_version(self.root, missing)

    def test_output_that_is_not_a_list_of_hits(self):
        for rc, out in ((0, ""), (0, "not json"), (0, "{}"), (1, "[]"), (2, "[]")):
            with self.assertRaises(SystemExit, msg=(rc, out)):
                ca.ruff_counts(self.root, self._runner(rc, out))

    def test_hits_and_no_hits(self):
        self.assertEqual(ca.ruff_counts(self.root, self._runner(0, "[]")), {})
        hit = json.dumps([{"filename": str(self.root / "a.py"), "code": "E741"},
                          {"filename": str(self.root / "a.py"), "code": None}])
        self.assertEqual(ca.ruff_counts(self.root, self._runner(1, hit)),
                         {"E741": {"a.py": 1}, "syntax": {"a.py": 1}})

    def test_a_hidden_hit_stays_counted(self):
        seen = []

        def run(argv, **_kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        ca.ruff_counts(self.root, run)
        self.assertIn("--ignore-noqa", seen[0])

    def test_the_version(self):
        self.assertEqual(ca.ruff_version(self.root, self._runner(0, "ruff 1.2.3\n")), "1.2.3")

    def test_the_child_python_cannot_import_from_the_cwd(self):
        self.assertEqual(ca._ruff_argv("--version")[:4], [sys.executable, "-I", "-m", "ruff"])

    def test_the_rule_set_is_read(self):
        _plant(self.root, {"pyproject.toml": ('[project]\nname = "x"\n# ignored\n'
                                              '[tool.ruff.lint]\nselect = ["E4"]\n\n[tool.other]\nk = 1\n'),
                           "sub/ruff.toml": "ignore = ['E741']\n"})
        self.assertEqual(ca.ruff_config(self.root), {
            "pyproject.toml": ["[tool.ruff.lint]", 'select = ["E4"]'],
            "sub/ruff.toml": ["ignore = ['E741']"]})


class TestLength(_Tree):
    def _body(self, name, n):
        return f"def {name}():\n" + "    x = 1\n" * (n - 1)

    def test_only_over_the_limit_and_named_with_its_class(self):
        _plant(self.root, {"src/a.py": (
            self._body("short", ca.MAX_LINES) + "class K:\n" + "".join(
                "    " + ln + "\n" for ln in self._body("big", ca.MAX_LINES + 1).splitlines()))})
        found = ca.long_functions(self.root)
        self.assertEqual(list(found), ["src/a.py::K.big"])
        self.assertEqual(found["src/a.py::K.big"][0][0], ca.MAX_LINES + 1)

    def test_a_pure_move_is_recorded_not_forgiven(self):
        cur = {"src/new.py::f": [(200, 1)]}
        grew, shrank = ca.compare_lengths(cur, {"src/old.py::f": [200]})
        self.assertEqual(grew, [])
        self.assertIn("f moved from src/old.py at 200 lines", shrank[0])

    def test_growth_a_new_name_and_a_shrink(self):
        grew, _ = ca.compare_lengths({"a.py::f": [(201, 1)]}, {"a.py::f": [200]})
        self.assertIn("baseline 200", grew[0])
        grew, _ = ca.compare_lengths({"a.py::g": [(151, 1)]}, {})
        self.assertIn("not on the list", grew[0])
        _, shrank = ca.compare_lengths({"a.py::f": [(199, 1)]}, {"a.py::f": [200]})
        self.assertIn("baseline 200", shrank[0])
        _, shrank = ca.compare_lengths({}, {"a.py::f": [200]})
        self.assertIn("gone or under", shrank[0])

    def test_one_function_shrinking_does_not_pay_for_another(self):
        base = {"a.py::main": [937], "b.py::main": [792]}
        grew, shrank = ca.compare_lengths({"a.py::main": [(100, 1)], "b.py::main": [(900, 1)]}, base)
        self.assertEqual(len(grew), 1)
        self.assertIn("b.py:1: main is 900 lines, baseline 792", grew[0])
        self.assertEqual(len(shrank), 1)

    def test_a_new_function_does_not_take_a_gone_ones_place(self):
        grew, shrank = ca.compare_lengths({"c.py::main": [(300, 1)]}, {"a.py::main": [937]})
        self.assertIn("not on the list", grew[0])
        self.assertIn("gone or under", shrank[0])

    def test_a_move_that_also_grew_is_growth(self):
        grew, _ = ca.compare_lengths({"new.py::f": [(201, 1)]}, {"old.py::f": [200]})
        self.assertIn("not on the list", grew[0])


class TestCounts(unittest.TestCase):
    def test_a_removed_hit_does_not_pay_for_a_new_one_elsewhere(self):
        grew, shrank = ca.compare_counts("ruff", {"BLE001": {"engine.py": 1}},
                                         {"BLE001": {"parser.py": 1}})
        self.assertIn("BLE001: engine.py: 1, baseline 0", grew[0])
        self.assertIn("BLE001: parser.py: 0, baseline 1", shrank[0])

    def test_growth_names_the_file(self):
        grew, shrank = ca.compare_counts("ruff", {"E741": {"a.py": 3}}, {"E741": {"a.py": 2}})
        self.assertIn("a.py: 3, baseline 2", grew[0])
        self.assertEqual(shrank, [])

    def test_a_rule_new_to_the_baseline_grows(self):
        grew, _ = ca.compare_counts("noqa", {"S310": {"a.py": 1}}, {})
        self.assertIn("S310: a.py: 1, baseline 0", grew[0])

    def test_a_lower_count_is_reported(self):
        _, shrank = ca.compare_counts("ruff", {}, {"E741": {"a.py": 2}})
        self.assertIn("E741: a.py: 0, baseline 2", shrank[0])

    def test_edges(self):
        grew, shrank = ca.compare_edges({"a -> b"}, {"b -> a"})
        self.assertIn("a -> b", grew[0])
        self.assertIn("b -> a", shrank[0])


class TestPrintCounts(unittest.TestCase):
    def test_it_counts_calls_in_src_by_the_parse(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _plant(root, {
                "src/a.py": ('print("x")\n# print("no")\ns = "print(1)"\n'
                             'out.print_it()\ndef f():\n    print()\n'),
                "src/quiet.py": "x = 1\n",
                "tests/t.py": 'print("a test")\n'})
            self.assertEqual(ca.print_counts(root), {"print": {"src/a.py": 2}})

    def test_a_file_that_does_not_parse_is_not_a_zero(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _plant(root, {"src/a.py": "print(\n"})
            with self.assertRaises(SyntaxError):
                ca.print_counts(root)

    def test_it_reads_a_file_again_when_it_changes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _plant(root, {"src/a.py": 'print("x")\n'})
            self.assertEqual(ca.print_counts(root), {"print": {"src/a.py": 1}})
            _plant(root, {"src/a.py": 'print("x")\nprint("y")\n'})
            self.assertEqual(ca.print_counts(root), {"print": {"src/a.py": 2}})

    def test_a_new_one_grows_and_a_gone_one_is_recorded(self):
        base = {"print": {"a.py": 2}}
        grew, _ = ca.compare_counts("print", {"print": {"a.py": 3}}, base)
        self.assertIn("a.py: 3, baseline 2", grew[0])
        _, shrank = ca.compare_counts("print", {"print": {"a.py": 1}}, base)
        self.assertIn("a.py: 1, baseline 2", shrank[0])

    def test_shrink_records_it_and_takes_nothing_new(self):
        base = {"max_function_lines": ca.MAX_LINES, "ruff_version": "1.0",
                "ruff_config": {"pyproject.toml": ["x"]},
                "import_edges": ["a -> b", "b -> a"],
                "ruff": {"E741": {"a.py": 3}, "F541": {"a.py": 1}},
                "noqa": {"F401": {"a.py": 1}},
                "function_length": {"a.py::f": [300], "a.py::g": [200]},
                "print_calls": {"print": {"a.py": 2, "b.py": 1}}}
        doc = ca._shrunk_doc(_state(print_calls={"print": {"a.py": 1, "c.py": 5}}), base)
        self.assertEqual(doc["print_calls"], {"print": {"a.py": 1}})


class TestMoneyCounts(_Tree):
    def test_a_bare_round_counts_and_a_method_does_not(self):
        _plant(self.root, {
            "src/a.py": ('x = round(1.5, 2)\ny = d.round(2)\nz = _round(3)\n# round(4)\n'
                         's = "round(5)"\ndef f():\n    return round(g, 2)\n'),
            "tests/t.py": "round(1.5)\n"})
        self.assertEqual(ca.money_counts(self.root)["round"], {"src/a.py": 2})

    def test_a_float_of_a_money_name_counts(self):
        _plant(self.root, {"src/a.py": "\n".join((
            'a = float(row["Proceeds"])',           # a string key, CamelCase
            "b = float(t.cost_cad)",                # an attribute
            "c = float(fees)",                      # a plural
            "d = float(e.get('net_amount') or 0)",  # a key in a call
            "e = float(totalCost)",                 # camelCase
            "f = float(qty)",                       # not money
            "g = float(r['quantity'])",
            "h = float('inf')",
            "i = float(sum(d.values()))",           # a method's name is not one
            "j = float(rate)",
            "k = float()",
            "l = d.float(price)",                   # not the builtin
            "m = float(GainRow(e).quantity or 0)",  # the view's class is not one
            "n = float(GainRow(e).proceeds or 0)",  # its money field is
            "o = float(net_gain(e))",               # a function's name is
            "p = float(Money(x))",                  # another class's is
        )) + "\n"})
        self.assertEqual(ca.money_counts(self.root)["float"], {"src/a.py": 8})

    def test_the_row_views_are_model_results(self):
        tree = ast.parse((REPO / "src/taxjson/model/result.py").read_text(encoding="utf-8"))
        views = {c.name for c in tree.body if isinstance(c, ast.ClassDef)
                 and (c.name == "Row" or any(getattr(b, "id", "") == "Row" for b in c.bases))}
        self.assertEqual(views, ca.ROW_VIEWS)

    def test_a_new_one_grows_and_shrink_records_a_gone_one(self):
        base = {"round": {"a.py": 4}, "float": {"a.py": 1}}
        grew, _ = ca.compare_counts("money", {"round": {"a.py": 5}, "float": {"a.py": 1}}, base)
        self.assertEqual(grew, ["money: round: a.py: 5, baseline 4"])
        doc = ca._shrunk_doc(_state(money={"round": {"a.py": 3}, "float": {"b.py": 2}}), TestShrink.BASE)
        self.assertEqual(doc["money"], {"round": {"a.py": 3}})

    def test_its_own_kind(self):
        self.assertIn("money", ca.KINDS)
        self.assertEqual(ca._kinds("money"), ["money"])


def _state(**kw):
    s = {"import_edges": {"a -> b", "b -> a"},
         "ruff_version": "1.0", "ruff_config": {"pyproject.toml": ["x"]},
         "ruff": {"E741": {"a.py": 3}, "F541": {"a.py": 1}},
         "noqa": {"F401": {"a.py": 1}},
         "function_length": {"a.py::f": [(300, 1)], "a.py::g": [(200, 9)]},
         "print_calls": {"print": {"a.py": 2}},
         "money": {"round": {"a.py": 4}, "float": {"a.py": 1}}}
    s.update(kw)
    return s


class TestShrink(unittest.TestCase):
    BASE = {"max_function_lines": ca.MAX_LINES, "ruff_version": "1.0",
            "ruff_config": {"pyproject.toml": ["x"]},
            "import_edges": ["a -> b", "b -> a"],
            "ruff": {"E741": {"a.py": 3}, "F541": {"a.py": 1}},
            "noqa": {"F401": {"a.py": 1}},
            "function_length": {"a.py::f": [300], "a.py::g": [200]},
            "print_calls": {"print": {"a.py": 2}},
            "money": {"round": {"a.py": 4}, "float": {"a.py": 1}}}

    def test_it_takes_every_improvement(self):
        doc = ca._shrunk_doc(_state(
            import_edges={"a -> b"}, ruff={"E741": {"a.py": 2}}, noqa={},
            function_length={"a.py::f": [(250, 1)]}), self.BASE)
        self.assertEqual(doc["import_edges"], ["a -> b"])
        self.assertEqual(doc["ruff"], {"E741": {"a.py": 2}})
        self.assertEqual(doc["noqa"], {})
        self.assertEqual(doc["function_length"], {"a.py::f": [250]})

    def test_it_never_adds(self):
        doc = ca._shrunk_doc(_state(
            import_edges={"a -> b", "b -> a", "c -> d"},
            ruff={"E741": {"a.py": 9, "b.py": 1}, "F541": {"a.py": 1}, "E401": {"a.py": 1}},
            noqa={"F401": {"a.py": 2}, "E402": {"a.py": 1}},
            function_length={"a.py::f": [(400, 1), (160, 2)], "a.py::g": [(200, 9)], "c.py::h": [(151, 1)]},
        ), self.BASE)
        self.assertEqual(doc["import_edges"], ["a -> b", "b -> a"])
        self.assertEqual(doc["ruff"], self.BASE["ruff"])
        self.assertEqual(doc["noqa"], self.BASE["noqa"])
        self.assertEqual(doc["function_length"], self.BASE["function_length"])

    def test_it_records_a_move_at_the_same_length(self):
        doc = ca._shrunk_doc(_state(function_length={"b.py::f": [(300, 1)], "a.py::g": [(200, 9)]}),
                             self.BASE)
        self.assertEqual(doc["function_length"], {"b.py::f": [300], "a.py::g": [200]})

    def test_a_hit_moved_to_another_file_is_not_recorded_as_moved(self):
        doc = ca._shrunk_doc(_state(ruff={"E741": {"a.py": 2, "b.py": 1}, "F541": {"a.py": 1}}),
                             self.BASE)
        self.assertEqual(doc["ruff"]["E741"], {"a.py": 2})

    def test_it_keeps_the_recorded_limit_and_rule_set(self):
        base = dict(self.BASE, max_function_lines=500, ruff_config={"pyproject.toml": ["y"]})
        doc = ca._shrunk_doc(_state(), base)
        self.assertEqual(doc["max_function_lines"], 500)
        self.assertEqual(doc["ruff_config"], {"pyproject.toml": ["y"]})


class TestAcceptGrowth(unittest.TestCase):
    BASE = TestShrink.BASE

    def test_it_records_only_the_kinds_named(self):
        doc = ca._accepted_doc(_state(
            import_edges={"a -> b", "c -> d"}, noqa={"F401": {"a.py": 9}},
            ruff={"E741": {"a.py": 9}}, function_length={"a.py::f": [(999, 1)]},
        ), self.BASE, ["imports"])
        self.assertEqual(doc["import_edges"], ["a -> b", "c -> d"])
        self.assertEqual(doc["noqa"], self.BASE["noqa"])
        self.assertEqual(doc["ruff"], self.BASE["ruff"])
        self.assertEqual(doc["function_length"], self.BASE["function_length"])

    def test_kinds_are_checked(self):
        self.assertEqual(ca._kinds("imports,length"), ["imports", "length"])
        self.assertEqual(ca._kinds("all"), list(ca.KINDS))
        with self.assertRaises(ca.argparse.ArgumentTypeError):
            ca._kinds("everything")


class TestMain(_Tree):
    """The whole script on a planted tree, ruff stood in for by a runner."""

    def setUp(self):
        super().setUp()
        _plant(self.root, {"src/taxjson/__init__.py": "",
                           "src/taxjson/lib/__init__.py": "",
                           "src/taxjson/lib/a.py": "v = 1\n",
                           "tests/x.py": "", "scripts/y.py": "", "packages/z.py": "",
                           "pyproject.toml": '[tool.ruff.lint]\nselect = ["F"]\n'})
        self.baseline = self.root / "baseline.json"
        self.version, self.hits = "ruff 1.0\n", []

    def _runner(self, argv, **_kw):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, self.version, "")
        rc = 1 if self.hits else 0
        return subprocess.CompletedProcess(argv, rc, json.dumps(self.hits), "")

    def _hit(self, code="E741", name="tests/x.py"):
        return {"filename": str(self.root / name), "code": code}

    def _main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ca.main(list(argv), root=self.root, baseline=self.baseline, runner=self._runner)
        return rc, out.getvalue()

    def _accept_all(self):
        self.assertEqual(self._main("--accept-growth=all")[0], 0)

    def test_a_plugin_reaching_bin_fails(self):
        # main's problems carry plugin_violations' (TJ-3542).
        self._accept_all()
        _plant(self.root, {"src/taxjson/bin/__init__.py": "", "src/taxjson/bin/t.py": "",
                           "packages/p/src/plug/__init__.py": "",
                           "packages/p/src/plug/a.py": "from taxjson.bin import t\n"})
        rc, out = self._main()
        self.assertEqual(rc, 1, out)
        self.assertIn("a plugin reads the core through", out)

    def test_an_edge_only_under_type_checking_passes_and_the_same_import_outside_fails(self):
        # b imports a; a imports b back only for the type checker: no cycle,
        # no new edge. The same import outside TYPE_CHECKING closes the
        # cycle: its two edges are new, not on the allow-list.
        self._accept_all()
        back = "from taxjson.lib import b\n"
        _plant(self.root, {"src/taxjson/lib/b.py": "from taxjson.lib import a\n",
                           "src/taxjson/lib/a.py": "from typing import TYPE_CHECKING\n\n"
                           "if TYPE_CHECKING:\n    " + back + "v = 1\n"})
        rc, out = self._main()
        self.assertEqual(rc, 0, out)
        _plant(self.root, {"src/taxjson/lib/a.py": back + "v = 1\n"})
        rc, out = self._main()
        self.assertEqual(rc, 1, out)
        self.assertIn("imports: new edge, not on the allow-list: taxjson.lib.a -> taxjson.lib.b", out)
        self.assertIn("imports: new edge, not on the allow-list: taxjson.lib.b -> taxjson.lib.a", out)

    def test_a_missing_baseline_is_not_a_clean_slate(self):
        self.assertEqual(self._main()[0], 2)
        self.assertEqual(self._main("--accept-growth=ruff")[0], 2)
        self.assertFalse(self.baseline.exists())

    def test_clean_then_worse_then_scoped_accept(self):
        self._accept_all()
        self.assertEqual(self._main()[0], 0)
        self.hits = [self._hit()]
        _plant(self.root, {"src/taxjson/lib/a.py": "import x  # noqa: F401\n"})
        rc, out = self._main()
        self.assertEqual(rc, 1)
        self.assertIn("--accept-growth=ruff,noqa", out)
        rc, out = self._main("--accept-growth=ruff")
        self.assertEqual(rc, 1, out)
        self.assertIn("noqa: F401", out)
        self.assertEqual(self._main("--accept-growth=noqa")[0], 0)

    def test_a_missing_ruff_leaves_the_baseline_alone(self):
        self._accept_all()
        before = self.baseline.read_text()
        self.version = ""
        with self.assertRaises(SystemExit):
            self._main("--shrink")
        self.assertEqual(self.baseline.read_text(), before)

    def test_a_whole_file_suppression_is_worse_though_its_hits_fall(self):
        self.hits = [self._hit()]
        self._accept_all()
        self.hits = []
        _plant(self.root, {"tests/x.py": "# ruff: noqa\n"})
        before = self.baseline.read_text()
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 1)
        self.assertIn("noqa: file:blanket: tests/x.py: 1, baseline 0", out)
        self.assertIn("Nothing was recorded", out)
        self.assertEqual(self.baseline.read_text(), before)

    def test_shrink_records_an_improvement_and_then_passes(self):
        self.hits = [self._hit()]
        self._accept_all()
        self.hits = []
        rc, out = self._main()
        self.assertEqual(rc, 1)
        self.assertIn("It got better", out)
        self.assertEqual(self._main("--shrink")[0], 0)
        self.assertEqual(json.loads(self.baseline.read_text())["ruff"], {})
        self.assertEqual(self._main()[0], 0)

    def test_shrink_takes_nothing_while_a_layer_rule_fails(self):
        self.hits = [self._hit()]
        self._accept_all()
        before = self.baseline.read_text()
        self.hits = []
        _plant(self.root, {"src/taxjson/engine/__init__.py": "",
                           "src/taxjson/bin/__init__.py": "",
                           "src/taxjson/engine/x.py": "from taxjson import bin\n"})
        self.assertEqual(self._main("--shrink")[0], 1)
        self.assertEqual(self.baseline.read_text(), before)

    def test_a_narrower_rule_set_is_worse_and_shrink_does_not_take_it(self):
        self._accept_all()
        _plant(self.root, {"pyproject.toml": '[tool.ruff.lint]\nselect = ["F"]\nignore = ["E741"]\n'})
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 1)
        self.assertIn("the rule set changed", out)
        self.assertEqual(self._main()[0], 1)

    def test_a_raised_limit_is_reported(self):
        self._accept_all()
        doc = json.loads(self.baseline.read_text())
        doc["max_function_lines"] = 100
        self.baseline.write_text(json.dumps(doc))
        before = self.baseline.read_text()
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 1)
        self.assertIn("recorded at 100", out)
        self.assertEqual(self.baseline.read_text(), before)

    def test_another_ruff_is_named_and_shrink_refuses(self):
        self.hits = [self._hit()]
        self._accept_all()
        self.version, self.hits = "ruff 2.0\n", []
        before = self.baseline.read_text()
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 2)
        self.assertIn("ruff 2.0 here", out)
        self.assertEqual(self.baseline.read_text(), before)
        rc, out = self._main()
        self.assertEqual(rc, 1)
        self.assertIn("that may be all this is", out)
        self.assertEqual(self._main("--accept-growth=ruff")[0], 0)
        self.assertEqual(self._main()[0], 0)

    def test_another_ruff_with_the_same_counts_passes(self):
        self._accept_all()
        self.version = "ruff 2.0\n"
        self.assertEqual(self._main()[0], 0)

    def test_a_syntax_error_names_the_file_and_records_nothing(self):
        self._accept_all()
        before = self.baseline.read_text()
        _plant(self.root, {"src/taxjson/lib/b.py": "def f(:\n"})
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 2)
        self.assertIn("b.py:1:", out)
        self.assertIn("Nothing was recorded", out)
        self.assertEqual(self.baseline.read_text(), before)

    def test_a_syntax_error_in_scripts_too(self):
        self._accept_all()
        _plant(self.root, {"scripts/y.py": "def f(:\n"})
        self.assertEqual(self._main()[0], 2)

    def test_the_summary_line_says_what_is_there_now(self):
        self._accept_all()
        self.hits = [self._hit(), self._hit(), self._hit()]
        rc, out = self._main()
        self.assertEqual(rc, 1)
        self.assertIn("3 ruff hits", out.splitlines()[0])

    def test_a_dotted_ruff_setting_is_refused_with_no_baseline_to_accept(self):
        _plant(self.root, {"pyproject.toml": '[tool]\nruff.lint.select = ["E9"]\n'})
        rc, out = self._main("--accept-growth=all")
        self.assertEqual(rc, 1)
        self.assertIn("outside a [tool.ruff...] table", out)

    def test_a_laundered_edge_does_not_shrink(self):
        # (A by-name edge on the allow-list: lib -> bin is a layer rule now.)
        _plant(self.root, {"src/taxjson/bin/__init__.py": "", "src/taxjson/bin/t.py": "",
                           "src/taxjson/cli/__init__.py": "",
                           "src/taxjson/cli/a.py": 'argv("taxjson.bin.t")\n'})
        self._accept_all()
        before = self.baseline.read_text()
        self.assertIn("taxjson.cli.a -> taxjson.bin.t (by name)", before)
        _plant(self.root, {"src/taxjson/util/__init__.py": "",
                           "src/taxjson/util/x.py": "from taxjson.bin import t\n",
                           "src/taxjson/cli/a.py": "from taxjson.util import x\n"})
        rc, out = self._main("--shrink")
        self.assertEqual(rc, 1)
        self.assertIn("`util` has no place", out)
        self.assertEqual(self.baseline.read_text(), before)

    def test_a_layer_rule_has_no_baseline_to_accept(self):
        _plant(self.root, {"src/taxjson/bin/__init__.py": "",
                           "src/taxjson/engine/__init__.py": "",
                           "src/taxjson/engine/x.py": "from taxjson import bin\n"})
        rc, out = self._main("--accept-growth=all")
        self.assertEqual(rc, 1)
        self.assertIn("taxjson.engine.x imports taxjson.bin", out)
        self.assertEqual(self._main()[0], 1)


class TestTheScriptItself(unittest.TestCase):
    def test_standard_library_only(self):
        tree = ast.parse((REPO / "scripts" / "check_architecture.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertLessEqual(names, {"argparse", "ast", "io", "json", "re", "subprocess", "sys", "tokenize",
                                     "collections", "functools", "pathlib"})


if __name__ == "__main__":
    unittest.main()

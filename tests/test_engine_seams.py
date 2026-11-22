"""tests/_engine.py, the engine's seams (PLAN.md step 10, B0b; TJ-3890).

The helper reaches every module of the engine, each module below
taxjson/engine/ (lib/core.py, the shim the batches left, went after
v1.0.0). The module list is checked against the package's files,
`source()` against their texts and definitions, `patch_engine` against
every module that defines or imports a name
(scripts/move_defs.py's exact seam count, `B0B_HELPER`, cannot tell what the
helper reaches: TJ-3928, TJ-3944)."""
import ast
import importlib.util
import re
import types
import unittest
from pathlib import Path
from unittest import mock

import _engine
import _paths
from taxjson.engine import consts

ENGINE = _paths.SRC / "taxjson" / "engine"


def _listed():
    """The engine package's files as the tree holds them (none before the
    first batch)."""
    return sorted(p.resolve() for p in ENGINE.rglob("*.py")) if ENGINE.is_dir() else []


def _text(m):
    with open(m.__file__, encoding="utf-8") as f:
        return f.read()


def _defined(text):
    """The top-level definitions of a module's text: def, class, assignment."""
    out = set()
    for n in ast.parse(text).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                out |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
    return out - {"__all__"}


def _imported(text):
    """The names a module's text binds by `from ... import`."""
    return {a.asname or a.name for n in ast.parse(text).body
            if isinstance(n, ast.ImportFrom) for a in n.names}


class TestTheModules(unittest.TestCase):
    def test_every_file_of_the_engine_is_a_module(self):
        # A module per file of the package, each the file it was imported
        # from.
        mods = _engine.modules()
        self.assertEqual(_engine.files(), [Path(m.__file__).resolve()
                                           for m in mods])
        self.assertEqual(_engine.files(), _listed())
        self.assertEqual([m.__name__ for m in mods], [
            ".".join(p.relative_to(_paths.SRC.resolve()).with_suffix("").parts).removesuffix(".__init__")
            for p in _listed()])

    def test_the_old_home_is_gone(self):
        # lib/core.py re-exported every engine name until the shims went
        # after v1.0.0: it is no module, and none of the helper's is it.
        self.assertIsNone(importlib.util.find_spec("taxjson.lib.core"))
        self.assertNotIn("taxjson.lib.core", [m.__name__ for m in _engine.modules()])

    def test_the_source_is_every_files_text(self):
        src = _engine.source()
        texts = [_text(m) for m in _engine.modules()]
        for m, text in zip(_engine.modules(), texts):
            with self.subTest(m.__name__):
                self.assertIn(text, src)
        self.assertEqual(src, "\n".join(texts))

    def test_the_source_defines_every_name_the_engine_defines(self):
        # The source holds every function and class a module of the
        # engine binds that is the engine's own.
        defined = _defined(_engine.source())
        own = {m.__name__ for m in _engine.modules()}
        names = sorted({k for m in _engine.modules() for k, v in vars(m).items()
                        if getattr(v, "__module__", None) in own and not k.startswith("__")})
        self.assertGreater(len(names), 200)
        self.assertEqual([k for k in names if k not in defined], [])


class TestPatchEngine(unittest.TestCase):
    def test_every_definer_and_importer_is_patched_and_restored(self):
        # Each name a module of the engine defines: inside the block it is
        # the new object in that module and in each module importing it;
        # after it, each holds the original again.
        texts = {m.__name__: (m, _text(m)) for m in _engine.modules()}
        checked = 0
        for name_m, (m, text) in texts.items():
            for name in sorted(_defined(text)):
                if name not in vars(m):
                    continue
                orig = vars(m)[name]
                readers = [m] + [o for o, t in texts.values()
                                 if o is not m and name in _imported(t)]
                new = object()
                with self.subTest(module=name_m, name=name):
                    with _engine.patch_engine(name, new) as got:
                        self.assertIs(got, new)
                        for r in readers:
                            self.assertIs(vars(r)[name], new, r.__name__)
                    for r in readers:
                        self.assertIs(vars(r)[name], orig, r.__name__)
                checked += 1
        self.assertGreater(checked, 250)

    def test_an_imported_name_is_patched_where_the_engine_binds_it(self):
        # `datetime` (tests/test_fix_1029_perf.py): bound by import in each
        # engine module reading it.
        from datetime import datetime
        binders = _engine.binders("datetime")
        self.assertTrue(binders)
        self.assertTrue(all(vars(m)["datetime"] is datetime for m in binders))
        every = [m for m in _engine.modules() if vars(m).get("datetime") is datetime]
        self.assertEqual(binders, every)
        with _engine.patch_engine("datetime", int):
            self.assertTrue(all(vars(m)["datetime"] is int for m in every))
        self.assertTrue(all(vars(m)["datetime"] is datetime for m in every))

    def test_a_name_bound_nowhere_or_to_two_objects_is_refused(self):
        with self.assertRaisesRegex(LookupError, "no module of the engine binds 'zz_nothing'"):
            with _engine.patch_engine("zz_nothing", 1):
                pass
        other = types.ModuleType("taxjson.engine.zz_fake")
        other._shown_if_float = object()
        with mock.patch.object(_engine, "modules", lambda: [consts, other]), \
                self.assertRaisesRegex(LookupError, "another object in taxjson.engine.zz_fake"):
            with _engine.patch_engine("_shown_if_float", 1):
                pass
        self.assertIsNot(consts._shown_if_float, 1)

    def test_store_engine_stays(self):
        name = "_shown_if_float"
        orig = _engine.binding(name)
        mods = _engine.binders(name)
        try:
            _engine.store_engine(name, len)
            self.assertTrue(all(vars(m)[name] is len for m in mods))
        finally:
            for m in mods:
                setattr(m, name, orig)
        self.assertIs(_engine.binding(name), orig)

    def test_the_loader_loads_this_helper_by_path(self):
        ns = {}
        exec(_engine.loader("E"), ns)
        self.assertEqual([m.__name__ for m in ns["E"].modules()],
                         [m.__name__ for m in _engine.modules()])


class TestTheFiles(unittest.TestCase):
    def test_each_marker_is_in_one_file(self):
        markers = re.findall(r"# cov: (\S+)\s*$", _engine.source(), re.M)
        self.assertEqual(len(markers), 7)
        for marker in markers:
            with self.subTest(marker):
                where = _engine.file_with(marker)
                self.assertIn(marker, Path(where).read_text(encoding="utf-8"))
                self.assertIn(where, [str(p) for p in _engine.files()])
        with self.assertRaisesRegex(AssertionError, "found in 0 files"):
            _engine.file_with("zz-no-such-marker")

    def test_the_site_pattern_names_each_file_and_no_other(self):
        pat = re.compile(_engine.site_pattern() + r":\d+$")
        for p in _engine.files():
            rel = p.relative_to(_paths.SRC.resolve()).as_posix()
            self.assertRegex(f"{rel}:12", pat)
        self.assertIsNone(pat.match("taxjson/lib/pipeline.py:12"))
        self.assertIsNone(pat.match("taxjson/lib/core.pyc:12"))


if __name__ == "__main__":
    unittest.main()

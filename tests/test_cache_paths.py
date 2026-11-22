"""The shared caches in $HOME have one binding of their path (step 8h).

~/.crypto_price_cache.json and ~/.currency_price_cache.json are read and
written by lib/crypto_prices and lib/rate_cache, which bind `CACHE_FILE`;
a test isolates a cache by patching it there. bin/fill_crypto_prices and
bin/to_base_curr read the path, the loaders and the price lookups the
tests fake through those modules and bind no copy: a patch of a copy in
bin would leave the moved loader on the real file in $HOME. So the bin
modules bind none of these names (a `mock.patch.object` of one fails)
and refuse a copy of one (lib/read_through: an assignment, setattr or
`patch.object(..., create=True)` raises, whatever the syntax reaching the
module; backlog TJ-3529), and no test file under tests/ assigns, patches
or reads one through a bin module, or touches a bin module's dict, which
the refusal cannot see (TJ-3535)."""
import ast
import unittest
from unittest import mock

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.bin import fill_crypto_prices, to_base_curr
from taxjson.lib import crypto_prices, rate_cache
import _paths

TESTS = _paths.TESTS
# What lib holds and bin reads through it: the names a test patches.
ONE_BINDING = {
    "taxjson.bin.fill_crypto_prices": (fill_crypto_prices, crypto_prices, {
        "CACHE_FILE", "load_cache", "save_cache", "get_crypto_price", "yahoo_id",
        "ids_seen_in_cache", "_utc_today"}),
    "taxjson.bin.to_base_curr": (to_base_curr, rate_cache, {"CACHE_FILE", "load_cache"}),
}


_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _imports_in(scope):
    """{alias: module} bound by the imports directly in `scope` (not in a
    function or class nested in it)."""
    out, todo = {}, list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        if isinstance(n, _SCOPES):
            continue
        if isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                out[a.asname or a.name] = f"{n.module}.{a.name}"
        elif isinstance(n, ast.Import):
            for a in n.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        todo.extend(ast.iter_child_nodes(n))
    return out


def _resolver(tree):
    """name_node -> the module its name is bound to by an import in the
    innermost scope that imports it (None when no import binds it)."""
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    cache = {}

    def resolve(node):
        name, n = node.id, node
        while n is not None:
            if isinstance(n, _SCOPES):
                if id(n) not in cache:
                    cache[id(n)] = _imports_in(n)
                if name in cache[id(n)]:
                    return cache[id(n)][name]
            n = parents.get(n)
        return None
    return resolve


class TestTheCachePathIsBoundOnce(unittest.TestCase):
    maxDiff = None
    def test_lib_holds_them_and_bin_binds_none(self):
        for name, (binmod, libmod, names) in ONE_BINDING.items():
            for n in sorted(names):
                with self.subTest(module=name, name=n):
                    self.assertTrue(hasattr(libmod, n))
                    self.assertFalse(hasattr(binmod, n))

    def test_bin_refuses_a_copy(self):
        for name, (binmod, libmod, names) in ONE_BINDING.items():
            for n in sorted(names):
                with self.subTest(module=name, name=n):
                    with self.assertRaisesRegex(AttributeError, f"set {libmod.__name__}.{n}"):
                        setattr(binmod, n, "/synthetic/cache.json")
                    # (mock's cleanup raises again; the refusal is its context)
                    with self.assertRaises(AttributeError) as cm:
                        with mock.patch.object(binmod, n, "/synthetic/cache.json", create=True):
                            pass
                    self.assertIn(f"set {libmod.__name__}.{n}",
                                  str(cm.exception.__context__ or cm.exception))
                    self.assertFalse(hasattr(binmod, n))
        # The rest of the module is set as before (a test fakes its main).
        with mock.patch.object(to_base_curr, "main", "faked"):
            self.assertEqual(to_base_curr.main, "faked")

    def test_no_test_reaches_one_through_bin(self):
        found = []
        for path in sorted(TESTS.rglob("*.py")):
            text = path.read_text("utf-8")
            if "fill_crypto_prices" not in text and "to_base_curr" not in text:
                continue
            tree = ast.parse(text)
            resolve = _resolver(tree)
            def module_of(x):
                """The bin module expression `x` is: an alias, the dotted
                name, sys.modules[...] (TJ-3538)."""
                if isinstance(x, ast.Name):
                    return resolve(x)
                if (isinstance(x, ast.Subscript) and ast.unparse(x.value) == "sys.modules"
                        and isinstance(x.slice, ast.Constant)):
                    return x.slice.value
                return ast.unparse(x)
            for n in ast.walk(tree):
                # The module's dict, which the refusal cannot see (TJ-3535).
                if (isinstance(n, ast.Attribute) and n.attr == "__dict__"
                        and module_of(n.value) in ONE_BINDING):
                    found.append(f"{path.relative_to(TESTS)}:{n.lineno} {ast.unparse(n)}")
                    continue
                if (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "vars"
                        and len(n.args) == 1 and module_of(n.args[0]) in ONE_BINDING):
                    found.append(f"{path.relative_to(TESTS)}:{n.lineno} {ast.unparse(n)}")
                    continue
                if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                    ref, attr = n.value, n.attr
                elif (isinstance(n, ast.Call) and len(n.args) >= 2
                        and isinstance(n.args[0], ast.Name)
                        and isinstance(n.args[1], ast.Constant)):
                    ref, attr = n.args[0], n.args[1].value
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    found += [f"{path.relative_to(TESTS)}:{n.lineno} {m}.{x}"
                              for m, (_, _, names) in ONE_BINDING.items()
                              for x in sorted(names | {"__dict__"}) if f"{m}.{x}" in n.value]
                    continue
                else:
                    continue
                module = resolve(ref)
                if module in ONE_BINDING and attr in ONE_BINDING[module][2]:
                    found.append(f"{path.relative_to(TESTS)}:{n.lineno} {ref.id}.{attr}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

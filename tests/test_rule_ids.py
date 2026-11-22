"""Step 7: the rules as constants (src/taxjson/rules/ids.py) and the
checks scripts/check_tax_rules.py runs on how src names them (9 to 12)."""
import ast
import importlib.util
import io
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from taxjson.lib import tax_logic as TL
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO
_spec = importlib.util.spec_from_file_location(
    "check_tax_rules_step7", REPO_ROOT / "scripts" / "check_tax_rules.py")
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)

KNOWN = {"CA-SL-01", "CA-SL-02", "US-WASH-22"}


def _write(folder: str, name: str, text: str) -> Path:
    p = Path(folder) / name
    p.write_text(text, encoding="utf-8")
    return p


class TestIdsModule(unittest.TestCase):
    def test_it_holds_the_catalog_one_constant_each(self):
        known = set(TL.catalog())
        self.assertEqual(set(R.ALL), known)
        self.assertEqual(R.CANADA | R.USA, R.ALL)
        self.assertFalse(R.CANADA & R.USA)
        self.assertEqual(K.ids_module_problems(known), [])
        self.assertEqual(R.CA_SL_02, "CA-SL-02")

    def test_the_province_table_names_every_province_the_estimate_has(self):
        # TJ-3494: one missing stops tax-logic with a KeyError.
        self.assertEqual(set(TL._ca_provinces()), set(R.CA_EST_PROVINCE_TABLES))

    def test_the_tables_hold_constants(self):
        for code, rid in R.CA_EST_PROVINCE_TABLES.items():
            self.assertEqual(rid, f"CA-EST-{code}-TABLE")
            self.assertIn(rid, R.ALL)
        for prefix, rids in R.CTRY.items():
            self.assertEqual(rids, tuple(f"{prefix}-CTRY-0{n}" for n in (1, 2, 3)))

    def test_a_missing_an_extra_and_a_misnamed_constant_are_named(self):
        fake = types.SimpleNamespace(CA_SL_01="CA-SL-01", CA_SL_09="CA-SL-09",
                                     US_WASH_99="US-WASH-22", other=1)
        out = "\n".join(K.ids_module_problems(KNOWN, fake))
        self.assertIn('CA_SL_09 = "CA-SL-09" is not a tax-logic rule', out)
        self.assertIn('US_WASH_99 = "US-WASH-22": a constant is named as its id', out)
        self.assertIn('CA-SL-02: tax-logic states it but src/taxjson/rules/ids.py '
                      'has no constant: add CA_SL_02 = "CA-SL-02"', out)

    def test_it_and_its_package_import_nothing_from_taxjson(self):
        # TJ-3499: relative imports count; other rules modules may import model.
        rules = REPO_ROOT / "src" / "taxjson" / "rules"
        for p in (rules / "ids.py", rules / "__init__.py"):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(node.level or (node.module or "").startswith("taxjson"), p)
                elif isinstance(node, ast.Import):
                    self.assertFalse(any(a.name.startswith("taxjson") for a in node.names), p)


class TestHowSrcNamesRules(unittest.TestCase):
    def test_the_tree_is_clean(self):
        problems, cited = K.source_id_problems(set(TL.catalog()), set(K.read_ids(K.RETIRED)))
        self.assertEqual(problems, [])
        self.assertIn("US-WASH-22", cited)

    def test_a_string_id_and_a_built_one_are_refused_a_message_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       'x = "CA-SL-02"\n'
                       'y = f"{p}-CTRY-01"\n'
                       'z = f"CA-EST-{code}-TABLE"\n'
                       'when = f"{y}-{m}-{dd}"\n'
                       'msg = "denied as superficial (CA-SL-02)"\n'
                       'n = R.CA_SL_01\n')
            known = KNOWN | {"CA-CTRY-01", "CA-EST-AB-TABLE"}
            problems, cited = K.source_id_problems(known, set(), files=[p])
        text = "\n".join(problems)
        self.assertIn('mod.py:1: the rule id "CA-SL-02" as a string: name it R.CA_SL_02', text)
        self.assertIn('mod.py:2: f"{}-CTRY-01" builds a rule id', text)
        self.assertIn('mod.py:3: f"CA-EST-{}-TABLE" builds a rule id', text)
        self.assertEqual(len(problems), 3, problems)
        self.assertEqual(sorted(cited), ["CA-SL-01", "CA-SL-02"])

    def test_a_family_typo_and_the_other_ways_to_build_an_id_are_refused(self):
        # TJ-3493: judged against the rules, not an id's shape.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       'a = "CA-SL-99"\n'
                       'b = "CA-SL-{:02d}".format(n)\n'
                       'c = "%s-SL-02" % p\n'
                       'd = "US-NY-2025"\n'
                       'e = f"CA-ON-{year}"\n'
                       'f = f"{base}-USD-PERP"\n'
                       'g = "denied ({}) under CA-SL-02".format(x)\n'
                       'h = "US-1099-B"\n'
                       'i = f"US-{state}" + f"CA-{prov}" + "US-%s" % s + "US-{}".format(s)\n'
                       'j = f"US{x}"\n'
                       'k = "%(c)s-SL-02" % d\n')
            problems, _ = K.source_id_problems(KNOWN, set(), files=[p])
        got = sorted((x[x.index("mod.py"):] for x in problems),
                     key=lambda x: int(x.split(":")[1]))
        # TJ-3502: a placeholder fills one segment; TJ-3503: a typo is a
        # citation of no rule (11), not advice to use a constant (10).
        self.assertEqual([int(x.split(":")[1]) for x in got], [1, 2, 3, 11], got)
        self.assertIn("mod.py:1: cites CA-SL-99, which is no tax-logic rule", got[0])

    def test_a_constant_must_name_a_rule(self):
        # TJ-3490: a retired rule's constant leaves ids.py; a use left
        # behind would raise AttributeError for the user.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       "x = R.CA_SL_02\n"
                       "y = R.US_META_01\n"
                       "z = R.CA_SL_98\n"
                       "t = R.CA_EST_PROVINCE_TABLES[code]\n")
            problems, cited = K.source_id_problems({"CA-SL-02"}, {"US-META-01"}, files=[p])
        problems = [x[x.index("mod.py"):] for x in problems]
        self.assertEqual(problems, [
            "mod.py:2: R.US_META_01 names US-META-01, a retired rule: name the "
            "statement that replaced it",
            "mod.py:3: R.CA_SL_98 is no rule id (src/taxjson/rules/ids.py)"])
        self.assertEqual(list(cited), ["CA-SL-02"])

    def test_src_imports_the_ids_one_way(self):
        # TJ-3501: R.NAME is every use only when R is the one alias.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       "from taxjson.rules import ids as R\n"
                       "import taxjson.rules.ids as rids\n"
                       "from taxjson.rules import ids\n"
                       "from taxjson.rules.ids import CA_SL_02\n"
                       "import taxjson.rules\n"
                       "from ..rules import ids as R\n"
                       "from .ids import CA_SL_02\n"
                       "from taxjson.lib import tax_logic as TL\n")
            problems, _ = K.source_id_problems(KNOWN, set(), files=[p])
        self.assertEqual(sorted(int(x[x.index("mod.py"):].split(":")[1]) for x in problems),
                         [2, 3, 4, 5, 6, 7], problems)
        self.assertIn("imports the rule ids another way: write "
                      "`from taxjson.rules import ids as R`", problems[0])

    def test_the_plugin_sources_are_read(self):
        # TJ-3496
        files = [f.as_posix() for f in K._src_files()]
        self.assertTrue(any("/packages/" in f and "/src/" in f for f in files))

    def test_a_citation_must_name_a_rule_and_not_a_retired_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       "# applies CA-SL-99 and US-META-01, and CA-SL-01\n"
                       '"""Not US-ASCII, not CA-ON, not a date 2025-01-02."""\n')
            problems, cited = K.source_id_problems(KNOWN, {"US-META-01"}, files=[p])
        problems = [x[x.index("mod.py"):] for x in problems]
        self.assertEqual(problems, [
            "mod.py:1: cites CA-SL-99, which is no tax-logic rule",
            "mod.py:1: cites US-META-01, a retired rule (tests/tax_rules/retired.txt): "
            "cite the statement that replaced it"])
        self.assertEqual(list(cited), ["CA-SL-01"])


class TestUncitedBaseline(unittest.TestCase):
    def test_it_only_shrinks(self):
        cited = {"CA-SL-01": ["src/a.py"]}
        seed = {"CA-SL-01", "CA-SL-02", "US-WASH-22"}
        self.assertEqual(K.uncited_problems(KNOWN, cited, ["CA-SL-02", "US-WASH-22"], seed, 2), [])
        out = K.uncited_problems(KNOWN, cited, ["CA-SL-01", "CA-SL-02"], seed, 2)
        self.assertIn("baseline-uncited.txt: CA-SL-01 is now cited (src/a.py)", out[0])
        self.assertIn("US-WASH-22 is cited nowhere in src outside the spec: name it "
                      "(R.US_WASH_22, or US-WASH-22 in a comment)", out[1])

    def test_a_new_rule_cannot_take_a_cited_ones_place(self):
        # TJ-3492: cite an old entry, baseline a new rule: same count, refused.
        cited = {"CA-SL-02": ["src/a.py"]}
        out = K.uncited_problems(KNOWN, cited, ["US-WASH-22"], {"CA-SL-02", "CA-SL-01"}, 1)
        self.assertEqual(len(out), 2, out)
        self.assertIn("baseline-uncited.txt: US-WASH-22 was not in the seed", out[0])
        self.assertIn("CA-SL-01 is cited nowhere", out[1])

    def test_a_deleted_line_cannot_come_back(self):
        # TJ-3500: cited, its line deleted, the cap lowered; uncited again,
        # the line is refused although the seed has it.
        seed = {"CA-SL-01", "CA-SL-02", "US-WASH-22"}
        out = K.uncited_problems(KNOWN, {}, ["CA-SL-01", "CA-SL-02", "US-WASH-22"], seed, 2)
        self.assertEqual(out, ["baseline-uncited.txt has 3 ids, more than UNCITED_MAX (2): it "
                               "only shrinks — cite the rule again where the code applies it"])
        self.assertIn("lower UNCITED_MAX in scripts/check_tax_rules.py to 1",
                      K.uncited_problems(KNOWN, {"CA-SL-01": ["a"], "CA-SL-02": ["a"]},
                                         ["US-WASH-22"], seed, 2)[0])

    def test_the_baseline_is_within_its_seed_and_cap(self):
        baseline = K.read_ids(K.UNCITED)
        self.assertLessEqual(set(baseline), set(K.read_ids(K.SEED_UNCITED)))
        self.assertEqual(len(baseline), K.UNCITED_MAX)


# The helpers that assert an engine rule (tests/_support/engine_rules.py):
# the position of their rule argument.
_ASSERTS = {"assert_rule": 3, "assert_noted": 2}


def _asserted(paths):
    """{rule id: [file, ...]}: the rules test files pass to `assert_rule` or
    `assert_noted` (tests/_support/engine_rules.py) by constant, the rule
    argument (`_ASSERTS`, or `rule=`) an `R.NAME` on the module's own import
    of the ids, or a constant imported from them (check_tax_rules
    `ids_bindings`), read with the AST: a string or any other call asserts
    none."""
    out = {}
    for path in paths:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        mods, consts = K.ids_bindings(tree)
        for n in ast.walk(tree):
            at = (_ASSERTS.get(getattr(n.func, "id", getattr(n.func, "attr", None)))
                  if isinstance(n, ast.Call) else None)
            if at is None:
                continue
            arg = (n.args[at] if len(n.args) > at
                   else next((k.value for k in n.keywords if k.arg == "rule"), None))
            name = (arg.attr if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name)
                    and arg.value.id in mods
                    else consts.get(arg.id) if isinstance(arg, ast.Name) else None)
            rid = getattr(R, name, None) if name else None
            if isinstance(rid, str):
                out.setdefault(rid, []).append(str(path))
    return out


class TestEngineRules(unittest.TestCase):
    """Check 12 for an engine rule (tax_logic.ENGINE_RULES, PLAN step 11):
    cited only where engine or characterize code names its constant, and
    never baselined; the tests' helper refuses any other rule on a record."""

    def test_only_a_constant_on_the_modules_own_import_names_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "mod.py",
                       "from taxjson.rules import ids as R\n"
                       "# applies CA-SL-01\n"
                       '"""and US-WASH-22."""\n'
                       "x = R.CA_SL_02\n"
                       "y = R.CA_SL_98\n")
            q = _write(d, "other.py", "x = R.CA_SL_01\n")    # no import of the ids
            named = K.engine_named([p, q])
        self.assertEqual(sorted(named), ["CA-SL-02"])
        self.assertTrue(named["CA-SL-02"][0].endswith("mod.py"))

    def test_an_engine_rule_counts_only_where_the_engine_names_it(self):
        cited = {"CA-SL-01": ["src/taxjson/lib/a.py"], "CA-SL-02": ["src/taxjson/engine/b.py"],
                 "US-WASH-22": ["src/taxjson/lib/a.py"]}
        named = {"CA-SL-02": ["src/taxjson/engine/b.py"]}
        self.assertEqual(K.uncited_problems(KNOWN, cited, [], set(KNOWN), 0,
                                            engine={"CA-SL-02"}, named=named), [])
        out = K.uncited_problems(KNOWN, cited, [], set(KNOWN), 0,
                                 engine={"CA-SL-01", "CA-SL-02"}, named=named)
        self.assertEqual(out, ["CA-SL-01 is an engine rule (tax_logic.ENGINE_RULES) that no "
                               "engine or characterize code names: name R.CA_SL_01 where the "
                               "record is made (a comment, a docstring or a string does not "
                               "count)"])

    def test_an_engine_rule_is_never_baselined_and_must_be_a_rule(self):
        cited = {"CA-SL-02": ["a"], "US-WASH-22": ["a"]}
        out = K.uncited_problems(KNOWN, cited, ["CA-SL-01"], set(KNOWN), 1,
                                 engine={"CA-SL-01"}, named={})
        self.assertEqual(len(out), 1, out)
        self.assertIn("baseline-uncited.txt: CA-SL-01 is an engine rule", out[0])
        out = K.uncited_problems(KNOWN, dict(cited, **{"CA-SL-01": ["a"]}), [], set(KNOWN), 0,
                                 engine={"CA-SL-99"}, named={})
        self.assertEqual(out, ["ENGINE_RULES: CA-SL-99 is not a tax-logic rule"])

    def test_the_tree_names_each_engine_rule_in_the_engine(self):
        named = K.engine_named()
        self.assertEqual(sorted(TL.ENGINE_RULES - set(named)), [])
        self.assertLessEqual(TL.ENGINE_RULES, set(TL.catalog()))
        self.assertFalse(TL.ENGINE_RULES & set(K.read_ids(K.UNCITED)))
        for rid, files in named.items():
            self.assertTrue(all(f.startswith(("src/taxjson/engine/", "src/taxjson/characterize/"))
                                for f in files), (rid, files))

    def test_a_test_asserts_each_engine_rule_on_a_record(self):
        # Check 12 counts an engine rule where engine code names its
        # constant, which a constant left in dead code satisfies. A test
        # also asserts each on a record its book makes: an `assert_rule`
        # call naming its constant (the review of step 11, F2).
        paths = sorted(p for p in (REPO_ROOT / "tests").rglob("*.py")
                       if "__pycache__" not in p.parts)
        self.assertEqual(sorted(TL.ENGINE_RULES - set(_asserted(paths))), [])

    def test_only_an_assert_rule_call_with_a_constant_asserts_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "from taxjson.rules import ids as R\n"
                       "from taxjson.rules.ids import CA_SL_09\n"
                       "assert_rule(self, res, 's1', R.CA_SL_01)\n"
                       "E.assert_rule(self, res, 's1', rule=CA_SL_09, record='r')\n"
                       "assert_rule(self, res, 's1', 'CA-SL-04')\n"
                       "assert_noted(self, heard, R.CA_ACB_08)\n"
                       "rules_of(res, R.CA_SL_12)\n"
                       "x = R.CA_SL_18\n")
            q = _write(d, "test_y.py", "assert_rule(self, res, 's1', R.CA_SL_19)\n")
            got = _asserted([p, q])
        self.assertEqual(sorted(got), ["CA-ACB-08", "CA-SL-01", "CA-SL-09"])

    def test_the_helper_refuses_a_rule_no_record_names(self):
        from _support.engine_rules import assert_rule, rules_of
        with self.assertRaises(AssertionError):
            assert_rule(self, {}, "s1", R.CA_RPT_01)
        with self.assertRaises(AssertionError):
            rules_of({"rules": {"s1": {"s1": R.CA_RPT_01}}}, "s1")
        self.assertEqual(rules_of({}, "s1"), {})


class TestMarkersTakeConstants(unittest.TestCase):
    def test_a_constant_reads_as_its_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "from taxjson.rules import ids as R\n"
                       "from taxjson.rules.ids import CA_SL_01\n"
                       "class T:\n"
                       "    @rule(R.CA_SL_02)\n"
                       "    def test_a(self): pass\n"
                       "    @rule(CA_SL_01, \"CA-SL-02\")\n"
                       "    def test_b(self): pass\n"
                       "    @rule(R.CA_SL_999)\n"
                       "    def test_c(self): pass\n"
                       "    @rule_absent(R.CA_SL_02, country=\"usa\")\n"
                       "    def test_d(self): pass\n"
                       "    @rule(R.CTRY[\"CA\"][0])\n"
                       "    def test_e(self): pass\n")
            marked = {m.node.name: m for m in K.collect([p])}
        self.assertEqual(marked["test_a"].rules[0][0], ("CA-SL-02",))
        self.assertEqual(marked["test_b"].rules[0][0], ("CA-SL-01", "CA-SL-02"))
        self.assertEqual(marked["test_c"].rules[0][0], ("R.CA_SL_999",))
        self.assertEqual(marked["test_d"].absent[0][:2], ("CA-SL-02", "usa"))
        # TJ-3495: a marker the checker cannot read is malformed, not silent.
        self.assertEqual(marked["test_e"].rules[0][0], ('<R.CTRY[\'CA\'][0]>',))
        self.assertIsNone(K.ID_RE.match("R.CA_SL_999"))

    def test_only_the_modules_own_import_of_the_ids_reads_as_a_rule(self):
        # TJ-3693: a constant's name alone pins nothing; its value may differ.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "import taxjson.rules.ids as RID\n"
                       "CA_ACB_01 = 'CA-SL-03'\n"
                       "class Fake:\n"
                       "    CA_SL_02 = 'CA-SL-01'\n"
                       "class T:\n"
                       "    @rule(RID.CA_SL_02)\n"
                       "    def test_a(self): pass\n"
                       "    @rule(Fake.CA_SL_02)\n"
                       "    def test_b(self): pass\n"
                       "    @rule(CA_ACB_01)\n"
                       "    def test_c(self): pass\n"
                       "    @rule(R.CA_SL_02)\n"
                       "    def test_d(self): pass\n")
            marked = {m.node.name: m.rules[0][0] for m in K.collect([p])}
        self.assertEqual(marked, {"test_a": ("CA-SL-02",), "test_b": ("<Fake.CA_SL_02>",),
                                  "test_c": ("<CA_ACB_01>",), "test_d": ("<R.CA_SL_02>",)})

    def test_a_test_a_later_one_replaces_pins_nothing(self):
        # TJ-3701 (check 14): collect() reads only the definitions that run.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "from taxjson.rules import ids as R\n"
                       "class T:\n"
                       "    @rule(R.CA_SL_01)\n"
                       "    def test_a(self): pass\n"
                       "    @rule(R.CA_SL_02)\n"
                       "    def test_a(self): pass\n"
                       "    def test_b(self): pass\n"
                       "    def test_b(self): pass\n"
                       "@rule(R.CA_SL_03)\n"
                       "class U:\n"
                       "    def test_c(self): pass\n"
                       "    def test_c(self): pass\n")
            marked = [(m.node.name, m.rules[0][0]) for m in K.collect([p]) if m.node.name != "U"]
            got = K.shadowed_problems([p])
        self.assertEqual(marked, [("test_a", ("CA-SL-02",)), ("test_c", ("CA-SL-03",))])
        self.assertEqual([g.split(": ", 1)[1].split(" is ")[0] for g in got],
                         ["T.test_a", "U.test_c"])
        self.assertIn("is defined again at line 6", got[0])

    def test_a_name_bound_again_or_elsewhere_reads_as_no_rule(self):
        # TJ-3716: each reads otherwise at run time than CA-SL-02 (CA-SL-01,
        # another value, or an error at import).
        probes = {
            "an import inside a function only": (                        # TJ-3740
                "def helper():\n    from taxjson.rules import ids as R\n"),
            "a match capture": ("from taxjson.rules import ids as R\n"
                                "match 1:\n    case R:\n        pass\n"),
            "except as": ("from taxjson.rules import ids as R\n"
                          "try:\n    pass\nexcept Exception as R:\n    pass\n"),
            "with as": ("import contextlib\nfrom taxjson.rules import ids as R\n"
                        "with contextlib.nullcontext() as R:\n    pass\n"),
            "a walrus in a comprehension": ("from taxjson.rules import ids as R\n"     # TJ-3739
                                            "_ = [(R := x) for x in (1,)]\n"),
            "an import inside a function": (
                "from types import SimpleNamespace\n"
                "def helper():\n    from taxjson.rules import ids as R\n"
                "R = SimpleNamespace(CA_SL_02='CA-SL-01')\n"),
            "rebound in the class body": "from taxjson.rules import ids as R\n",
            "rebound after the import": ("from taxjson.rules import ids as R\n"
                                          "R = type('F', (), {'CA_SL_02': 'CA-SL-01'})\n"),
            "a constant rebound": ("from taxjson.rules.ids import CA_SL_02\n"
                                   "CA_SL_02 = 'CA-SL-01'\n"),
            "a global in a function": ("from taxjson.rules import ids as R\n"
                                       "def f():\n    global R\n    R = None\n"),
            "a star import": "from taxjson.rules import ids as R\nfrom os.path import *\n",
        }
        for why, head in probes.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as d:
                body = ("class T:\n"
                        + ("    R = type('F', (), {'CA_SL_02': 'CA-SL-01'})\n"
                           if why == "rebound in the class body" else "")
                        + "    @rule(" + ("CA_SL_02" if why == "a constant rebound" else "R.CA_SL_02")
                        + ")\n    def test_a(self): pass\n")
                p = _write(d, "test_x.py", head + body)
                rules = [m.rules[0][0] for m in K.collect([p])]
                self.assertEqual(len(rules), 1)
                self.assertTrue(rules[0][0].startswith("<"), rules)
        # A parameter, a local, a nonlocal or a comprehension variable named R
        # leaves the module's readable (TJ-3738, TJ-3740).
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py", "from taxjson.rules import ids as R\n"
                                       "def f(R=None):\n    R = 1\n    return [R for R in ()]\n"
                                       "def g():\n    R = 1\n    def h():\n        nonlocal R\n"
                                       "        R = 2\n"
                                       "_ = [R for R in ()]\n"
                                       "class T:\n    @rule(R.CA_SL_02)\n    def test_a(self): pass\n")
            self.assertEqual(K.collect([p])[0].rules[0][0], ("CA-SL-02",))
        # An alias in a from-import reads as its constant (TJ-3721).
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py", "from taxjson.rules.ids import CA_SL_01 as ONE\n"
                                       "class T:\n    @rule(ONE)\n    def test_a(self): pass\n")
            self.assertEqual(K.collect([p])[0].rules[0][0], ("CA-SL-01",))

    def test_a_malformed_constant_names_the_import_to_use(self):
        # TJ-3720: main's check 1 says what a marker may read.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tests").mkdir()
            _write(str(root / "tests"), "test_x.py",
                   "import taxjson.rules.ids\nclass T:\n"
                   "    @rule(taxjson.rules.ids.CA_SL_02)\n    def test_a(self): pass\n")
            out = io.StringIO()
            with mock.patch.object(K, "ROOT", root), mock.patch.object(K, "TESTS", root / "tests"), \
                    redirect_stdout(out):
                K.main([])
        self.assertIn("@rule('<taxjson.rules.ids.CA_SL_02>'): malformed id: name the rule as "
                      "R.CA_XX_01, R a module-level `from taxjson.rules import ids as R` that "
                      "nothing else binds", out.getvalue())

    def test_what_replaces_a_test_or_a_class_is_any_later_binding(self):
        # TJ-3717, TJ-3718: a class defined again, a name assigned or deleted,
        # a marked class nested; an overload stub and a property's accessors not.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "from typing import overload\n"
                       "from taxjson.rules import ids as R\n"
                       "class T:\n"                                   # 3: replaced at 7
                       "    @rule(R.CA_SL_01)\n"
                       "    def test_x(self): pass\n"
                       "@rule(R.CA_SL_02)\n"
                       "class T:\n"
                       "    @rule(R.CA_SL_01)\n"
                       "    def test_a(self): pass\n"                 # 9: assigned at 10
                       "    test_a = None\n"
                       "    @rule(R.CA_SL_01)\n"
                       "    def test_b(self): pass\n"                 # 12: deleted at 13
                       "    del test_b\n"
                       "    @overload\n"
                       "    def test_c(self): pass\n"
                       "    def test_c(self): pass\n"
                       "    @property\n"
                       "    def p(self): return 1\n"
                       "    @p.setter\n"
                       "    def p(self, v): pass\n"
                       "    class Inner:\n"
                       "        @rule(R.CA_SL_01)\n"
                       "        def test_i(self): pass\n"
                       "@rule(R.CA_SL_03)\n"
                       "def test_mod(): pass\n"
                       "def test_mod(): pass\n")
            got = [g.split(": ", 1)[1] for g in K.shadowed_problems([p])]
            marked = sorted({(m.cls.name if m.cls is not None else "", m.node.name)
                             for m in K.collect([p])})
        self.assertEqual([g.split(", so")[0] for g in got], [
            "T is defined again at line 7", "T.test_a is bound again at line 10",
            "T.test_b is deleted at line 13",
            "Inner is not at module level: the check reads only module-level classes",
            "test_mod is a module-level function: unittest runs only a TestCase's methods"])
        # The second T runs: its class marker on test_c and p; no first T, no
        # Inner, and no module-level function (unittest runs none; TJ-3735).
        self.assertEqual(marked, [("T", "T"), ("T", "p"), ("T", "test_c")])

    def test_a_wrap_keeps_a_test_and_a_branch_may_replace_it(self):
        # TJ-3733: a wrap, a deleted mixin its subclasses run; TJ-3734: a
        # replacement in a branch; TJ-3740: an import or an annotated
        # assignment replaces, an annotation alone does not.
        src = ("import unittest\n"
               "from taxjson.rules import ids as R\n"
               "class _Run:\n"
               "    @rule(R.CA_SL_01)\n"
               "    def test_x(self): pass\n"
               "    @rule(R.CA_SL_02)\n"
               "    def test_w(self): pass\n"
               "    test_w = unittest.expectedFailure(test_w)\n"
               "class TestCa(_Run, unittest.TestCase): pass\n"
               "@rule(R.CA_SL_03)\n"
               "class TestSkip(unittest.TestCase):\n"
               "    def test_s(self): pass\n"
               "TestSkip = unittest.skipIf(False, 'x')(TestSkip)\n"
               "del _Run\n"
               "class TestB(unittest.TestCase):\n"
               "    @rule(R.CA_SL_01)\n"
               "    def test_b(self): pass\n"
               "    if True:\n"
               "        def test_b(self): pass\n"
               "@rule(R.CA_SL_02)\n"
               "class TestC(unittest.TestCase):\n"
               "    def test_c(self): pass\n"
               "try:\n"
               "    class TestC(unittest.TestCase): pass\n"
               "except ImportError:\n"
               "    pass\n"
               "@rule(R.CA_SL_02)\n"
               "class TestD(unittest.TestCase):\n"
               "    def test_d(self): pass\n"
               "del TestD\n"
               "class TestE(unittest.TestCase):\n"
               "    @rule(R.CA_SL_01)\n"
               "    def test_e(self): pass\n"
               "    from os import path as test_e\n"
               "    @rule(R.CA_SL_01)\n"
               "    def test_f(self): pass\n"
               "    test_f: object = None\n"
               "    @rule(R.CA_SL_01)\n"
               "    def test_g(self): pass\n"
               "    test_g: object\n"
               "@rule(R.CA_SL_01)\n"
               "def test_top(): pass\n")
        lines = src.splitlines()

        def ln(s):
            return lines.index(s) + 1
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py", src)
            got = [g.split(": ", 1)[1].split(", so")[0] for g in K.shadowed_problems([p])]
            marked = sorted((m.cls.name if m.cls is not None else "", m.node.name)
                            for m in K.collect([p]))
        self.assertEqual(got, [
            f"TestB.test_b is defined again at line {ln('        def test_b(self): pass')}, in a branch",
            f"TestC is defined again at line {ln('    class TestC(unittest.TestCase): pass')}, in a branch",
            f"TestD is deleted at line {ln('del TestD')}",
            f"TestE.test_e is bound again at line {ln('    from os import path as test_e')}",
            f"TestE.test_f is bound again at line {ln('    test_f: object = None')}",
            "test_top is a module-level function: unittest runs only a TestCase's methods"])
        self.assertEqual(marked, [("TestE", "test_g"), ("TestSkip", "TestSkip"), ("TestSkip", "test_s"),
                                  ("_Run", "test_w"), ("_Run", "test_x")])

    def test_a_string_marker_is_refused(self):
        # Check 13 (PLAN step 9 B0c): a test names a rule by its constant.
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_x.py",
                       "class T:\n"
                       "    @rule(R.CA_SL_02)\n"
                       "    def test_a(self): pass\n"
                       "    @rule(R.CA_SL_01, \"CA-SL-02\")\n"
                       "    def test_b(self): pass\n"
                       "    @rule_absent(\"CA-SL-02\", country=\"usa\")\n"
                       "    def test_c(self): pass\n"
                       "    @tax_rules.rule(\"US-WASH-01\")\n"
                       "    def test_d(self): pass\n"
                       "    def test_e(self):\n"
                       "        src = '@rule(\"CA-SL-02\")'\n")
            got = K.string_marker_problems([p])
        self.assertEqual([(g.split(":")[1], g.split(": ", 2)[1]) for g in got],
                         [("4", "@rule('CA-SL-02')"), ("6", "@rule_absent('CA-SL-02')"),
                          ("8", "@rule('US-WASH-01')")])
        self.assertIn("R.US_WASH_01", got[2])
        # The module's own alias when it has one (TJ-3696).
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, "test_y.py", "from taxjson.rules import ids as RID\n"
                                       "class T:\n"
                                       "    @rule(\"CA-SL-02\")\n"
                                       "    def test_a(self): pass\n")
            got = K.string_marker_problems([p])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].endswith("name the rule by its constant, RID.CA_SL_02"), got[0])
        # No import yet: the one to add, RID where the module's R is taken (TJ-3720, TJ-3721).
        for src, want in (("", "R.CA_SL_02 (`from taxjson.rules import ids as R` at the top)"),
                          ("R = 1\n", "RID.CA_SL_02 (`from taxjson.rules import ids as RID` "
                                       "at the top)"),
                          ("class _Fx:\n    R = 1.47\n",                            # TJ-3737
                           "RID.CA_SL_02 (`from taxjson.rules import ids as RID` at the top)"),
                          ("R = 1\nRID = 2\n", "RULE_IDS.CA_SL_02 (`from taxjson.rules import "
                                                "ids as RULE_IDS` at the top)")):
            with self.subTest(src), tempfile.TemporaryDirectory() as d:
                p = _write(d, "test_z.py", src + "class T:\n    @rule(\"CA-SL-02\")\n"
                                                 "    def test_a(self): pass\n")
                got = K.string_marker_problems([p])
                self.assertTrue(got[0].endswith(want), got[0])

    def test_main_runs_checks_13_and_14(self):
        # TJ-3694: main itself, over a test tree holding one of each.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tests").mkdir()
            _write(str(root / "tests"), "test_x.py",
                   "from taxjson.rules import ids as R\n"
                   "class T:\n"
                   "    @rule(\"CA-SL-02\")\n"
                   "    def test_a(self): pass\n"
                   "    @rule(R.CA_SL_01)\n"
                   "    def test_b(self): pass\n"
                   "    def test_b(self): pass\n")
            out = io.StringIO()
            with mock.patch.object(K, "ROOT", root), mock.patch.object(K, "TESTS", root / "tests"), \
                    redirect_stdout(out):
                K.main([])
        text = out.getvalue()
        # (rc is 1 over any such tree: the rules nothing pins; TJ-3719.)
        self.assertIn("tests/test_x.py:3: @rule('CA-SL-02'): name the rule by its constant, "
                      "R.CA_SL_02", text)
        self.assertIn("tests/test_x.py:6: T.test_b is defined again at line 7", text)
        # Check 4's advice names the constant (TJ-3691).
        self.assertRegex(text, r"US-WASH-01 has no test \(add @rule\(R\.US_WASH_01\) to the test")


class TestWhere(unittest.TestCase):
    """`check_tax_rules --where RULE...`: the tests that pin a rule,
    wherever they sit (the index of the tests by rule, PLAN.md step 9)."""

    @staticmethod
    def _where(*argv):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = K.main(list(argv))
        return rc, out.getvalue()

    @classmethod
    def setUpClass(cls):
        # Two runs shared by the tests (each reads every marker).
        cls.family = cls._where("--where", "CA-SL", "--summary")
        cls.unknown = cls._where("--where", "CA-NOPE-01", "CA-S")

    def test_a_rule_lists_its_pinning_tests_by_path_and_line(self):
        rc, out = self.family
        self.assertEqual(rc, 0, out)
        lines = out.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.startswith("CA-SL-02: "))
        self.assertRegex(lines[i], r"^CA-SL-02: \d+ pinning")
        block = []
        for ln in lines[i + 1:]:
            if not ln.startswith("  "):
                break
            if not ln.startswith("  absent: "):
                block.append(ln.split()[0])
        self.assertIn("TestCraGoldenExamples.test_cra_least_of_three_formula", out)
        keys = [(w.rpartition(":")[0], int(w.rpartition(":")[2])) for w in block]
        self.assertEqual(keys, sorted(keys))

    def test_a_family_lists_each_rule_and_an_option_is_no_rule(self):
        rc, out = self.family
        heads = [ln.split(":")[0] for ln in out.splitlines() if not ln.startswith("  ")]
        self.assertIn("CA-SL-01", heads)
        self.assertTrue(all(h.startswith("CA-SL-") for h in heads), heads)
        self.assertNotIn("--summary", out)

    def test_an_unknown_id_or_a_prefix_short_of_a_family_is_said_and_fails(self):
        rc, out = self.unknown
        self.assertEqual(rc, 1)
        self.assertIn("CA-NOPE-01: tax-logic states no such rule or family", out)
        self.assertIn("CA-S: tax-logic states no such rule or family", out)
        self.assertNotIn("CA-SL-02", out)

    def test_a_marker_problem_read_past_is_said_and_fails(self):
        # TJ-3673: a marker naming no rule is in no list; --where says so.
        import contextlib
        import io
        out = io.StringIO()
        bad = "tests/test_y.py:9 T.test_u: @rule('CA-SL-99'): tax-logic states no such rule"
        with contextlib.redirect_stdout(out):
            rc = K._where(["CA-SL-02"], {"CA-SL-02"}, {"CA-SL-02": ["tests/test_x.py:3 T.test_t"]},
                          {}, [bad])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue().splitlines(),
                         ["CA-SL-02: 1 pinning", "  tests/test_x.py:3 T.test_t", f"tax-rules: {bad}"])

    def test_a_mixins_markers_are_listed_once_where_they_are_defined(self):
        # TJ-3988: a tests/_support mixin's marked test is listed once, at
        # its line, not once per TestCase built on it (two here).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tests" / "_support").mkdir(parents=True)
            _write(str(root / "tests" / "_support"), "__init__.py", "")
            _write(str(root / "tests" / "_support"), "shared.py",
                   "from tax_rules import rule\n"
                   "from taxjson.rules import ids as R\n"
                   "class FooCases:\n"
                   "    @rule(R.CA_SL_02)\n"
                   "    def test_a(self): pass\n"
                   "    def test_b(self): pass\n")
            for name in ("test_one.py", "test_two.py"):
                _write(str(root / "tests"), name,
                       "import unittest\n"
                       "from _support.shared import FooCases\n"
                       "class TestFoo(FooCases, unittest.TestCase):\n"
                       "    pass\n")
            rc, out = self._where_in(root, "--where", "CA-SL-02")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines(),
                         ["CA-SL-02: 1 pinning", "  tests/_support/shared.py:5 FooCases.test_a"])

    @staticmethod
    def _where_in(root, *argv):
        with mock.patch.object(K, "ROOT", root), mock.patch.object(K, "TESTS", root / "tests"):
            return TestWhere._where(*argv)

    def test_where_alone_says_what_it_needs(self):
        rc, out = self._where("--where")
        self.assertEqual(rc, 2)
        self.assertIn("--where needs a rule id or a family", out)

if __name__ == "__main__":
    unittest.main()

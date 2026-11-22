"""The refactor's step 1: the CLI left bin/taxjson_run.py and stays out.

Formerly: test_refactor_step1"""
import argparse
import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from _run_patch import _holders, patch_run
from taxjson.bin import taxjson_run as R
from taxjson.cli import parser
from taxjson.cli.common import _self_cmd
from taxjson.pipeline.dispatch import run_cmd
from taxjson.pipeline import messages, run_state, stages, tools
import _paths

SRC = _paths.REPO / "src" / "taxjson"


class TestTheEntryPointHoldsNoCode(unittest.TestCase):
    def test_only_imports_their_list_and_the_main_guard(self):
        tree = ast.parse((SRC / "bin" / "taxjson_run.py").read_text("utf-8"))
        rest = [n for n in tree.body[1:]
                if not isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual([type(n).__name__ for n in rest], ["Assign", "If"],
                         "new code goes in taxjson.cli or taxjson.pipeline")
        names, guard = rest
        self.assertEqual(names.targets[0].id, "__all__")
        self.assertIsInstance(ast.literal_eval(names.value), tuple)
        self.assertEqual(
            ast.dump(guard),
            ast.dump(ast.parse('if __name__ == "__main__":\n    main()\n').body[0]))

    def test_no_command_module_over_3000_lines(self):
        long = [p.name for p in (SRC / "cli").glob("*.py")
                if p.read_text("utf-8").count("\n") > 3000]
        self.assertEqual(long, [])


class TestEachGroupRegistersItsCommands(unittest.TestCase):
    """A command's parser sits beside its handler: `register` in the group
    module, called by `_build_parser`, which keeps only `help` (it closes over
    the top parser)."""
    NOT_GROUPS = {"__init__", "checklist_run", "common", "main", "parser"}

    @staticmethod
    def _registered_by(group):
        sub = argparse.ArgumentParser().add_subparsers()
        group.register(sub)
        return sub.choices

    def test_a_new_group_module_is_on_the_list(self):
        on_disk = {p.stem for p in (SRC / "cli").glob("*.py")} - self.NOT_GROUPS
        self.assertEqual({g.__name__.rsplit(".", 1)[1] for g in parser._GROUPS},
                         on_disk)

    def test_every_command_comes_from_one_group(self):
        names = [n for g in parser._GROUPS for n in self._registered_by(g)]
        self.assertEqual(len(names), len(set(names)))
        _p, sub = parser._build_parser()
        self.assertEqual(set(names) | {"help"}, set(sub.choices))

    def test_a_command_is_registered_by_the_module_of_its_handler(self):
        # `sanity`'s handler is in the pipeline, which `run` calls; pipeline
        # must not import cli, so its group registers it.
        strays = []
        for g in parser._GROUPS:
            for name, sp in self._registered_by(g).items():
                where = sp.get_default("func").__module__
                if where != g.__name__ and name != "sanity":
                    strays.append((name, g.__name__, where))
        self.assertEqual(strays, [])

    def test_the_parser_module_adds_only_help(self):
        text = (SRC / "cli" / "parser.py").read_text("utf-8")
        self.assertEqual(re.findall(r"""add_parser\(\s*["']([\w-]+)""", text),
                         ["help"])


class TestAMessageGoesThroughOut(unittest.TestCase):
    """docs/output-style.md: `out.show` / `out.emit`, not `print`: they owe the
    blank line after a message of more than one line. Report text on stdout can
    still be printed: `scripts/check_architecture.py` counts that, and the count
    only falls. `out.act`'s one-line `! ` items are report text here. What this
    can't see: a message in a variable, unless a labelling helper made it in the
    same call; a helper of another name; and bin/ and lib/, not scanned (the
    stages' captured bytes are bin's, and step 1 is the commands and the run)."""
    LABEL = re.compile(r"\s*(?:(?:info|warning|error|note|attention)\s*:"
                       r"|(?:\{\}|[\w-]+(?: [\w-]+)*): (?:note|warning|error)\b)",
                       re.I)
    FORMATTERS = {"exit_text", "labelled", "_labelled", "_labelled_note", "message"}
    # A stream that is stdout, or stdout unless --json asks for stderr: plain lines.
    REPORT_STREAMS = {"sys.stdout", "out"}

    @classmethod
    def _text(cls, node):
        """The words a string expression starts with, `{}` for what is filled in."""
        while isinstance(node, ast.BinOp):
            node = node.left
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"):
            node = node.func.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "{}"
                           for v in node.values)
        return node.value if (isinstance(node, ast.Constant)
                              and isinstance(node.value, str)) else None

    @classmethod
    def _names_formatter(cls, node):
        return any(isinstance(n, ast.Call)
                   and getattr(n.func, "id", getattr(n.func, "attr", "")) in cls.FORMATTERS
                   for n in ast.walk(node))

    @classmethod
    def _message(cls, call):
        """Why this print() is a message, or None. A bare print(file=...) has no
        words, and a prompt (`end=""`) waits on the same line."""
        if not call.args or any(
                k.arg == "end" and isinstance(k.value, ast.Constant) and k.value.value == ""
                for k in call.keywords):
            return None
        first = call.args[0]
        if cls._names_formatter(first):
            return "a labelling helper"
        for k in call.keywords:
            if k.arg == "file" and ast.unparse(k.value) not in cls.REPORT_STREAMS:
                return f"to {ast.unparse(k.value)}"
        text = cls._text(first)
        return "a label" if text is not None and cls.LABEL.match(text) else None

    @classmethod
    def _hits(cls, tree):
        """(line, why) for each message printed, or written, raw."""
        found = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print":
                why = cls._message(n)
                if why:
                    found.append((n.lineno, why))
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "write" and n.args
                  and ast.unparse(n.func.value) == "sys.stderr"
                  and cls._text(n.args[0]) is not None):
                # Not a write of a variable: a child's captured stderr, relayed.
                found.append((n.lineno, "sys.stderr.write"))
            elif isinstance(n, ast.For) and cls._names_formatter(n.iter):
                found += [(c.lineno, "a loop over a labelling helper")
                          for b in n.body for c in ast.walk(b)
                          if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "print"]
        return found

    def test_no_command_or_stage_prints_one(self):
        bad = []
        for folder in ("cli", "pipeline"):
            for p in sorted((SRC / folder).glob("*.py")):
                bad += [f"{folder}/{p.name}:{line}: {why}"
                        for line, why in self._hits(ast.parse(p.read_text("utf-8")))]
        self.assertEqual(bad, [], "lib/out.py: `show`, `emit`, `warn`, `error`")

    def test_the_check_tells_a_message_from_report_text(self):
        def said(code):
            return self._hits(ast.parse(code))
        for message in ('print("x", file=sys.stderr)', 'print(f"Warning: {x}")',
                        'print("ATTENTION: x")', 'print(f"tjs sum: warning: {x}")',
                        'print(labelled(x, stream), file=stream)', 'print(exit_text(x))',
                        'print(f"{_PROG}: warning: {x}")', 'print(f"warning: {x}")',
                        'print("note: x")', 'print(f"taxjson: error: {x}")',
                        'print(out.labelled(x))', 'print("Warning: x", end="\\n")',
                        'print("x", file=_err)', 'sys.stderr.write("x")',
                        'print("Warning: {}".format(x))',
                        'print("\\n".join(out.message("warning", x)))',
                        'for ln in _out_wrap(_labelled_note(x)):\n    print(ln)',
                        'for _ln in _out_wrap(_labelled(x)):\n    print(_ln)'):
            self.assertTrue(said(message), message)
        for text in ("print()", "print(file=sys.stderr)", 'print("EVENTS")',
                     'print(f"  {row}")', 'print(_act(x, y))',
                     'print("tjs -C x run")', 'print(q, end="", file=sys.stderr)',
                     'print(text, file=out)', 'print("x", file=sys.stdout)',
                     'sys.stdout.write("x")', 'print("Date: 2025")',
                     'sys.stderr.write(res.stderr)', 'print(e.message)',
                     'print("{} rows".format(n))', 'print(wrap(x))'):
            self.assertEqual(said(text), [], text)


class TestTheEntryPointReExportsOnlyWhatIsImported(unittest.TestCase):
    """Tests import from where a name lives. What is left is `main`, what src
    still imports from here, and what a published taxjson-fetch imports: an
    older plugin on a newer core must keep working."""
    REPO = SRC.parent.parent
    # What published taxjson-fetch releases import. Fixed, not read from the
    # plugin here: this tree's plugin moving off them changes nothing for those.
    PUBLISHED_PLUGIN = {"detect_broker", "input_files"}
    # Every bin route a published plugin takes, by module (this tree's plugin
    # imports taxjson.pipeline.plugin_api; step 8).
    PUBLISHED_ROUTES = {"taxjson.bin.taxjson_run": PUBLISHED_PLUGIN,
                        "taxjson.bin.taxjson_detect_brokerage": {"looks_like_ib_text"}}

    def _read_from_run(self, *roots):
        """The names read from the entry point: from-imports, and `alias.name`,
        in code or in a child script held in a string. Not import_module."""
        names = set()
        for root in roots:
            for path in root.rglob("*.py"):
                text = path.read_text("utf-8")
                if path.name == "taxjson_run.py" or "taxjson_run" not in text:
                    continue
                for node in ast.walk(ast.parse(text)):
                    if (isinstance(node, ast.ImportFrom)
                            and node.module == "taxjson.bin.taxjson_run"):
                        names.update(a.name for a in node.names)
                for alias in set(re.findall(r"\btaxjson_run as (\w+)", text)):
                    names.update(re.findall(
                        rf"(?<![\w.]){alias}\.([A-Za-z_]\w*)", text))
        return names - {"__all__", "__file__"}

    def test_the_list_is_exact(self):
        wanted = self._read_from_run(SRC) | self.PUBLISHED_PLUGIN | {"main"}
        self.assertEqual(set(R.__all__), wanted)

    def test_this_trees_plugin_reads_nothing_from_here(self):
        self.assertEqual(
            self._read_from_run(self.REPO / "packages" / "taxjson-fetch" / "src"), set())

    def test_a_published_plugins_routes_still_reach_the_plugin_api(self):
        import importlib
        from taxjson.pipeline import plugin_api
        for module, names in sorted(self.PUBLISHED_ROUTES.items()):
            mod = importlib.import_module(module)
            for name in sorted(names):
                with self.subTest(module=module, name=name):
                    self.assertIs(getattr(mod, name), getattr(plugin_api, name))
        # Each route's name is offered there (more may be: TJ-3544).
        self.assertLessEqual(set().union(*self.PUBLISHED_ROUTES.values()),
                             set(plugin_api.__all__))

    def test_no_test_reads_a_name_from_here(self):
        names = self._read_from_run(self.REPO / "tests",
                                    self.REPO / "packages" / "taxjson-fetch"
                                    / "tests")
        self.assertLessEqual(names, {"main"})


class TestARunsStateIsOneObject(unittest.TestCase):
    """What a run's stages leave for each other is `run_state.current`, not a
    container at the top of a module."""
    TYPES = {"Counter", "OrderedDict", "defaultdict", "deque", "dict", "list", "set"}
    CHANGES = {"add", "append", "appendleft", "clear", "difference_update",
               "discard", "extend", "insert", "intersection_update",
               "move_to_end", "pop", "popitem", "popleft", "remove", "reverse",
               "setdefault", "sort", "subtract", "symmetric_difference_update",
               "update"}

    @staticmethod
    def _trees():
        return [ast.parse(p.read_text("utf-8")) for folder in ("cli", "pipeline")
                for p in (SRC / folder).glob("*.py")]

    @classmethod
    def _containers(cls, tree):
        found = set()
        for n in tree.body:
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, ast.AnnAssign):
                targets = [n.target]
            else:
                continue
            v = n.value
            called = getattr(v, "func", None)
            called = getattr(called, "id", getattr(called, "attr", None))
            if called in cls.TYPES or isinstance(
                    v, (ast.Dict, ast.DictComp, ast.List, ast.ListComp,
                        ast.Set, ast.SetComp)):
                found.update(t.id for t in targets if isinstance(t, ast.Name))
        return found

    @classmethod
    def _changed(cls, tree, names):
        found = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            own = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)} | {
                x.id for x in ast.walk(fn)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
            for n in ast.walk(fn):
                target = None
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in cls.CHANGES):
                    target = n.func.value
                elif isinstance(n, ast.Subscript) and isinstance(n.ctx, (ast.Store, ast.Del)):
                    target = n.value
                elif isinstance(n, ast.AugAssign):
                    target = n.target
                if isinstance(target, ast.Name) and target.id not in own:
                    found.add(target.id)
                elif (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)):
                    # Another module's: `common._X.add(1)`.
                    found.add(target.attr)
        return found & names

    def test_no_module_keeps_a_container_it_changes(self):
        trees = self._trees()
        names = set().union(*(self._containers(t) for t in trees))
        changed = set().union(*(self._changed(t, names) for t in trees))
        self.assertEqual(changed, set(),
                         "what a run keeps goes in pipeline/run_state.py")

    def test_the_check_sees_each_way_of_keeping_one(self):
        for made in ("set()", "[]", "{}", "dict()", "defaultdict(list)",
                     "collections.defaultdict(list)", "OrderedDict()",
                     "{k: 1 for k in ()}", "[k for k in ()]", "Counter()"):
            for change in ("X.add(1)", "X[1] = 2", "del X[1]", "X.sort()",
                           "X.difference_update({1})", "other.X.add(1)",
                           "other.X[1] = 2"):
                with self.subTest(made=made, change=change):
                    tree = ast.parse(f"X = {made}\ndef f():\n    {change}\n")
                    self.assertEqual(
                        self._changed(tree, self._containers(tree)), {"X"})

    def test_a_local_of_that_name_is_not_one(self):
        tree = ast.parse("X = []\ndef f(X):\n    X.sort()\n"
                         "def g():\n    X = []\n    X.sort()\n")
        self.assertEqual(self._changed(tree, self._containers(tree)), set())

    def test_the_state_is_always_read_through_the_module(self):
        # `begin_run` replaces the object: a copy, even a local one, keeps
        # writing to the old run's. TJ-1412 replaces it once per command too.
        bad = []
        for p in sorted(SRC.rglob("*.py")):
            text = p.read_text("utf-8")
            if "run_state" not in text:
                continue
            for n in ast.walk(ast.parse(text)):
                for child in ast.iter_child_nodes(n):
                    if (isinstance(child, ast.Attribute) and child.attr == "current"
                            and getattr(child.value, "id", "") == "run_state"
                            and not (isinstance(n, ast.Attribute) and n.value is child)):
                        bad.append(f"{p.name}:{child.lineno}")
        self.assertEqual(bad, [], "`run_state.current.x`, every time")

    def test_a_new_run_starts_clean(self):
        with mock.patch.object(run_state, "current", run_state.RunState()):
            old = run_state.current
            old.shown.add("x")
            old.parsed[Path("p")] = (1, 2)
            old.details_hidden = old.echo_last_error = True
            run_state.begin_run()
            self.assertIsNot(run_state.current, old)
            self.assertEqual(run_state.current, run_state.RunState())


class TestSharedValuesKeepOneBinding(unittest.TestCase):
    """A from-import copies a binding: a module reading a rebound value that way
    keeps the old one, and --details or the command's name goes wrong in that
    module only."""

    def test_no_module_rebinds_a_global(self):
        bad = []
        for folder in ("cli", "pipeline"):
            for p in sorted((SRC / folder).glob("*.py")):
                if p.name == "run_state.py":
                    continue
                for n in ast.walk(ast.parse(p.read_text("utf-8"))):
                    if isinstance(n, ast.Global):
                        bad.append(f"{folder}/{p.name}:{n.lineno}: {n.names}")
        self.assertEqual(bad, [], "what is rebound goes in pipeline/run_state.py")

    def test_run_state_is_only_reached_as_a_module(self):
        bad = []
        for p in sorted(SRC.rglob("*.py")):
            for n in ast.walk(ast.parse(p.read_text("utf-8"))):
                if isinstance(n, ast.ImportFrom) \
                        and n.module == "taxjson.pipeline.run_state":
                    bad.append(f"{p.relative_to(SRC)}:{n.lineno}")
        self.assertEqual(bad, [], "`from taxjson.pipeline import run_state`")


class TestPatchRun(unittest.TestCase):
    """A fake reaches every module holding the name, or the test would pass
    without its fake ever running."""

    def test_every_holder_is_patched_and_restored(self):
        real = tools.run_to_file
        fake = mock.Mock()
        with patch_run("run_to_file", fake) as got:
            self.assertIs(got, fake)
            for mod in (tools, stages):
                self.assertIs(mod.run_to_file, fake)
        for mod in (tools, stages):
            self.assertIs(mod.run_to_file, real)

    def test_a_name_no_module_binds_is_an_error(self):
        with self.assertRaises(AttributeError):
            patch_run("no_such_name_anywhere").start()

    def test_two_objects_under_one_name_is_an_error(self):
        with mock.patch.object(stages, "_step", object(), create=True), \
                self.assertRaisesRegex(AttributeError, "different objects"):
            _holders("_step")
        self.assertTrue(_holders("_step"))
        self.assertIn(messages, _holders("_step"))


class TestNestedCommandKeepsTheOutersName(unittest.TestCase):
    """TJ-1408, TJ-1410: `tjs amt` runs the estimate in this process; on an
    installed `tjs` its errors then said `tjs estimate: error`, and `--details`
    was off for what followed."""

    def _nested_main(self, nested):
        with mock.patch.object(run_state, "current_cmd", "amt"), \
                mock.patch.object(run_state, "current_details", True), \
                mock.patch.object(run_state, "in_main", True), \
                mock.patch("taxjson.lib.cli_diag.run_top_level", nested):
            try:
                R.main()
            except SystemExit as e:
                self.assertEqual(e.code, 1)
            self.assertEqual((run_state.current_cmd, run_state.current_details,
                              run_state.in_main), ("amt", True, True))

    def test_restored_when_the_nested_command_returns(self):
        def nested(_prog, _main, **_kw):
            run_state.current_cmd = "estimate"
            run_state.current_details = False
        self._nested_main(nested)

    def test_restored_when_the_nested_command_fails(self):
        def nested(_prog, _main, **_kw):
            run_state.current_cmd = "estimate"
            run_state.current_details = False
            raise SystemExit(1)
        self._nested_main(nested)


class TestCtrlCInANestedCommand(unittest.TestCase):
    """TJ-1413: it became rc 130 from dispatch.run_cmd, which a caller reads as
    a failed child: close-year went on with a carry-back of 0."""

    def test_it_reaches_the_command_running_it(self):
        def interrupted():
            raise KeyboardInterrupt
        with patch_run("_main", interrupted), \
                mock.patch.object(run_state, "in_main", True), \
                self.assertRaises(KeyboardInterrupt):
            run_cmd(_self_cmd(Path("."), "carryover", "--json"),
                    capture_output=True)

    def test_the_outer_command_stops_with_one_line_and_exit_130(self):
        code = (f"import sys; sys.path.insert(0, {str(_paths.TESTS)!r})\n"
                "from pathlib import Path\n"
                "from _run_patch import patch_run\n"
                "from taxjson.bin import taxjson_run as R\n"
                "from taxjson.cli.common import _self_cmd\n"
                "from taxjson.pipeline.dispatch import run_cmd\n"
                "calls = []\n"
                "def fake():\n"
                "    calls.append(1)\n"
                "    if len(calls) > 1:\n"
                "        raise KeyboardInterrupt\n"
                "    run_cmd(_self_cmd(Path('.'), 'carryover', '--json'),\n"
                "            capture_output=True)\n"
                "    print('carried on after the interrupt')\n"
                "patch_run('_main', fake).start()\n"
                "R.main()\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 130, r.stderr)
        self.assertNotIn("carried on", r.stdout)
        self.assertEqual(r.stderr.count("interrupted"), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()

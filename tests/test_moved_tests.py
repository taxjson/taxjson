"""The moved test modules (PLAN.md step 9): the `Formerly:` lines that
scripts/move_tests.py writes, scripts/moved.py reads, and the mover on a
synthetic repository.

- Every `Formerly:` line names a module that no longer exists at the top
  of tests/, and no module is the whole-file source of two but a split
  one, whose every file names it and gives each test class its line.
- scripts/moved.py finds each.
- The mover moves a module into its package, ends its docstring with the
  line (its line ends kept), re-keys the architecture baseline, names it
  dotted where a script runs it (a script module moved too), rewrites its
  path, never the tools, lowers the round-named count, and lists the
  mentions it leaves (not a shared helper's `Shared from:` line, which
  is history: TJ-3996); it refuses a batch it cannot do whole before it
  changes anything (a module missing or untracked, a target taken, a
  folder no package, a path git ignores or ruff skips), and undoes one
  that fails after.
- The verifier compares two git trees, not the work tree: its text check
  passes a clean move and fails each edit a move must not carry (code, a
  comment, src, a fixture, a new file, a package's __init__.py or one
  outside tests/rules/ and tests/features/, an executable one, the tools,
  a deletion, the baseline, a mode, a symlink, a staged edit the work
  tree hides, a copy, a file not text, a rename or the ratchet reverted,
  a move by hand the mover would refuse); docstrings, docs and an old
  name settled to that move's new one are listed, nothing else. Its
  --summary and wiring comparisons fail a difference; the copy that runs
  is BASE's, isolated; a repository with replace refs is refused.
- A split (`OLD::Class NEW_PATH`) on a synthetic repository: OLD's lines
  cut by statement (comments, blank lines, line ends kept), the hooks,
  what runs at import and a helper two files reach in each, a mixin with
  its class, an import cut to the names a file reads, each class's
  `Formerly:` line, the baseline's counts (a copy's again), a script's
  `OLD.Class` renamed, the ids one to one; each refusal before any
  change, and one undone; the verifier passing it and failing it
  tampered (a line changed, a class dropped or copied, a line moved, a
  hook missing, the module kept, the baseline's counts)."""
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
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


MT = _load("move_tests")
MV = _load("moved")
VT = _load("verify_test_moves")
# The note listing the digit runs a comparison leaves out.
LEFT_OUT = ("digit runs left out, the BASE runs disagreeing on them (BASE's -> HEAD's, each "
            "within BASE's range widened by its spread): ")
# The layout test, read from the mover, as RUN_MODULE is (TJ-3769):
# spelled here, its path would be rewritten when a batch moves the real one.
LAYOUT = MT.LAYOUT.as_posix()


def formerly_problems(tests):
    """What is wrong with the `Formerly:` lines below TESTS: a module named
    that is still there, one moved whole to two places (a split module
    apart: each of its files names it once, and gives each class there
    that runs tests its class line), a name moved.py does not find."""
    fmap = MV.formerly_map(tests)
    splits = MV.split_modules(fmap)
    out = []
    for old, places in fmap.items():
        mod = old.split(".")[0]
        if (tests / f"{mod}.py").exists():
            out.append(f"{old}: tests/{mod}.py still exists")
        whole = [rel for rel, cls in places if not cls and "." not in old]
        if old in splits:
            lost = sorted(set(splits[old]) - set(whole))
            if lost:
                out.append(f"{old}: {lost} hold its classes and do not name it")
            for rel in sorted(whole):
                tree = ast.parse((tests / rel).read_text(encoding="utf-8"))
                for c in tree.body:
                    if isinstance(c, ast.ClassDef) and c.name in MV.test_classes(tree) \
                            and f"{old}.{c.name}" not in MT.class_lines(c):
                        out.append(f"{rel}: {c.name} runs tests and has no `Formerly: "
                                   f"{old}.{c.name}` line")
        elif len(whole) > 1:
            out.append(f"{old} moved whole to {whole}")
        if not MV.where(old, fmap, tests):
            out.append(f"{old}: scripts/moved.py finds no place")
    return out


class TestFormerlyLines(unittest.TestCase):
    def test_each_names_a_module_gone_and_none_twice(self):
        # A split module is named by each of its files, every class of it
        # by its own line.
        self.assertEqual(formerly_problems(_paths.TESTS), [])

    def test_the_run_files_and_the_layout_test_are_where_the_lines_say(self):
        # A line dropped after a move leaves a run file, or the layout test,
        # where the verifier would look for it in vain (TJ-3804).
        for rel in (*MT.run_files(_paths.TESTS), MT.LAYOUT.as_posix()):
            where = MT.moved_path(_paths.TESTS, rel)
            self.assertTrue((_paths.REPO / where).is_file(), f"{rel}: not at {where}")
        self.assertTrue(MT.layout_path(_paths.TESTS).is_file())


class TestDocstringLine(unittest.TestCase):
    def test_the_line_ends_the_docstring(self):
        self.assertEqual(MT.add_formerly('"""One."""\nx = 1\n', "test_fix_a"),
                         '"""One.\n\nFormerly: test_fix_a"""\nx = 1\n')
        self.assertEqual(MT.add_formerly('"""Two\nlines.\n"""\n', "test_fix_a"),
                         '"""Two\nlines.\n\nFormerly: test_fix_a\n"""\n')
        self.assertEqual(MT.add_formerly("x = 1\n", "test_fix_a"),
                         '"""Formerly: test_fix_a"""\nx = 1\n')
        with self.assertRaises(ValueError):
            MT.add_formerly('"one quote"\n', "test_fix_a")

    def test_the_column_counts_characters(self):
        # The AST's end column counts UTF-8 bytes (TJ-3680).
        self.assertEqual(MT.add_formerly('"""Caf\u00e9 \u2014 ok."""\nx = 1\n', "test_fix_a"),
                         '"""Caf\u00e9 \u2014 ok.\n\nFormerly: test_fix_a"""\nx = 1\n')
        self.assertEqual(MT.add_formerly('"""Two\n\u00e9 lines.\n"""\n', "test_fix_a"),
                         '"""Two\n\u00e9 lines.\n\nFormerly: test_fix_a\n"""\n')

    def test_line_ends_and_first_lines_are_kept(self):
        self.assertEqual(MT.add_formerly('"""One."""\r\nx = 1\r\n', "test_fix_a"),
                         '"""One.\r\n\r\nFormerly: test_fix_a"""\r\nx = 1\r\n')
        self.assertEqual(
            MT.add_formerly("#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nx = 1\n", "test_fix_a"),
            '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"""Formerly: test_fix_a"""\nx = 1\n')
        self.assertEqual(MT.add_formerly("# a comment\nx = 1\n", "test_fix_a"),
                         '"""Formerly: test_fix_a"""\n# a comment\nx = 1\n')
        # A coding line counts on line one or two only (TJ-3714).
        self.assertEqual(MT.add_formerly("# a comment\n# coding: utf-8\nx = 1\n", "test_fix_a"),
                         '# a comment\n# coding: utf-8\n"""Formerly: test_fix_a"""\nx = 1\n')

    def test_lines_are_split_as_the_parser_splits_them(self):
        # Not at a form feed or a Unicode separator (TJ-3710).
        self.assertEqual(MT.split_lines("a\x0cb\r\nc\rd\u2028e\nf"),
                         ["a\x0cb\r\n", "c\r", "d\u2028e\n", "f"])
        self.assertEqual(MT.add_formerly('# \x0c\n"""One."""\nx = 1\n', "test_fix_a"),
                         '# \x0c\n"""One.\n\nFormerly: test_fix_a"""\nx = 1\n')

    def test_a_path_is_renamed_whole(self):
        olds = {"test_fix_a": "rules/ca/sl/test_b.py"}
        self.assertEqual(
            MT.rename_text("docs/x.md", "tests/test_fix_a.py, packages/p/tests/test_fix_a.py "
                           "tests/test_fix_a.py.bak tests/test_fix_ab.py `tests/test_fix_a.py`.",
                           olds),
            "tests/rules/ca/sl/test_b.py, packages/p/tests/test_fix_a.py "
            "tests/test_fix_a.py.bak tests/test_fix_ab.py `tests/rules/ca/sl/test_b.py`.")
        self.assertEqual(MT.rename_text("backlog.md", "tests/test_fix_a.py", olds),
                         "tests/test_fix_a.py")
        self.assertEqual(MT.rename_text("scripts/mutation_audit.py", '"test_fix_a.TestX"', olds),
                         '"rules.ca.sl.test_b.TestX"')
        self.assertEqual(MT.rename_text("docs/x.md", '"test_fix_a"', olds), '"test_fix_a"')

    def test_a_name_is_renamed_only_as_a_module(self):
        rx = MT.name_pattern("test_fix_a")
        self.assertEqual(rx.sub("rules.ca.sl.test_b", '"test_fix_a" test_fix_a.TestX '
                                "tests/test_fix_a.py test_fix_ab"),
                         '"rules.ca.sl.test_b" rules.ca.sl.test_b.TestX '
                         "tests/test_fix_a.py test_fix_ab")

    def test_a_placement_line_is_checked(self):
        self.assertEqual(MT.read_placement("# a comment\ntest_fix_a rules/ca/sl/test_b.py\n"),
                         [("test_fix_a", "rules/ca/sl/test_b.py")])
        for bad in ("test_fix_a test_b.py", "test_fix_a rules/ca/sl/b.py",
                    "test_fix_a rules/../test_b.py", "fix_a rules/ca/test_b.py",
                    "test_fix_a rules/ca/test_b.py\ntest_fix_a rules/us/test_c.py",
                    # only into rules/ or features/, never a private folder (TJ-3682)
                    "test_fix_a rules/test_b.py", "test_fix_a _hermetic/x/test_b.py",
                    "test_fix_a golden/ca/test_b.py", "test_fix_a taxjson/x/test_b.py",
                    "test_fix_a rules/_support/test_b.py", "test_fix_a features/a-b/test_b.py",
                    # a folder a module's name would shadow (TJ-3708)
                    "test_fix_a features/test_x/test_b.py"):
            with self.assertRaises(SystemExit, msg=bad):
                MT.read_placement(bad)


_LAYOUT_TEXT = ('"""Layout."""\nimport re\nROUND_NAMED_MAX = 1\n'
                '_ROUND_NAMED = re.compile(r"test_(fix(?:[a-z]\\d*)?|audit|decisions)_\\w*\\.py\\Z")\n'
                '_ROUND_WORDS = re.compile(r"test_\\w*_review\\.py\\Z")\n'
                '_NOT_ROUND_NAMED = ("test_audit_command.py",)\n')


def _synthetic(r: Path, extra=None) -> str:
    """A small repository for the tools (a module to move, one that stays,
    the files a move rewrites, and src, a fixture and a tool that it must
    not; EXTRA files too); its tree, staged, as the base (no commit: the
    PII hook refuses a made-up identity)."""
    files = {
        "tests/test_fix_demo.py": '"""Demo tests."""\nimport unittest\n\n\n'
                                  "class TestX(unittest.TestCase):\n"
                                  "    def test_a(self):\n        pass\n",
        "tests/test_other.py": '"""Stays."""\nimport unittest\n\n\n'
                               "class TestO(unittest.TestCase):\n"
                               "    def test_o(self):\n        pass\n",
        LAYOUT: _LAYOUT_TEXT,
        "tests/fixtures/f.csv": "a,b\n1,2\n",
        "scripts/architecture-baseline.json": json.dumps(
            {"ruff": {"E402": {"tests/test_fix_demo.py": 2, "tests/x.py": 1}},
             "noqa": {}}, indent=1) + "\n",
        "scripts/mutation_audit.py":
            'TEST_CMDS = [["py", "-m", "unittest", "test_fix_demo", "test_other"]]\n',
        "scripts/ci_no_extras.sh": 'EXTRAS_TESTS="test_fix_demo test_other"\n',
        "scripts/moved.py": 'x = "tests/test_fix_demo.py"\n',
        ".github/workflows/tests.yml": "    run: python -m unittest test_fix_demo\n",
        "src/x.py": "COUNTRY = 'CA'\n",
        "docs/map.md": "- `tests/test_fix_demo.py` — `TestX`\n",
        "docs/notes.md": "see test_fix_demo for the demo\n",
        "CHANGELOG.md": "- tests/test_fix_demo.py was added.\n",
        "backlog.md": "- Location: `tests/test_fix_demo.py`\n",
        ".gitignore": "inputs/\n",
        **(extra or {}),
    }
    for rel, text in files.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            (r / rel).write_bytes(text)
        else:
            (r / rel).write_text(text)
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    return _git(r, "write-tree").strip()


def _git(r: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=r, check=True, capture_output=True,
                          text=True).stdout


MOVE = [("test_fix_demo", "rules/ca/sl/test_demo.py")]
# The test module the mover names in RUN_FILES, read from it: spelled here,
# its path would be rewritten when a batch moves the real one.
RUN_MODULE = next(f for f in MT.RUN_FILES if f.startswith("tests/"))
# Shared helpers naming the module they came from (TJ-3996): the line is
# history in a tests/_support/ module's docstring and a plugin's helper's
# (the last closes the docstring); not in a string, a comment or a test module.
SHARED = {
    "tests/_support/__init__.py": '"""Shared test helpers."""\n',
    "tests/_support/demo_rows.py": '"""Shared test helpers: the demo rows.\n\n'
                                   'Shared from: test_fix_demo\n"""\n'
                                   'NOTE = """\nShared from: test_fix_demo\n"""\n'
                                   "# Shared from: test_fix_demo, a comment\n",
    "packages/p/tests/_helper.py": '"""Shared test helpers: p.\n\n'
                                   'Shared from: test_fix_demo, test_other"""\n',
    "packages/p/tests/test_plugged.py": '"""Plugged.\n\nShared from: test_fix_demo\n"""\n',
    "tests/test_wire.py": '"""Wire.\n\nShared from: test_fix_demo\n"""\n',
}


class TestTheMover(unittest.TestCase):
    def test_a_move_on_a_synthetic_repository(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base_tree = _synthetic(r)
            left = MT.move(r, MOVE)
            new = r / "tests" / "rules" / "ca" / "sl" / "test_demo.py"
            self.assertTrue(new.is_file())
            self.assertFalse((r / "tests" / "test_fix_demo.py").exists())
            for d in ("rules", "rules/ca", "rules/ca/sl"):
                self.assertTrue((r / "tests" / d / "__init__.py").read_text().startswith('"""'))
            self.assertIn("CA-SL", (r / "tests/rules/ca/sl/__init__.py").read_text())
            # a family named for its id though its folder is not (FAMILY_FOLDERS)
            self.assertEqual(MT.package_doc(("rules", "us", "distributions")),
                             "The tests of rule family US-DIST.")
            self.assertTrue(new.read_text().startswith('"""Demo tests.\n\nFormerly: test_fix_demo"""'))
            base = json.loads((r / "scripts" / "architecture-baseline.json").read_text())
            self.assertEqual(base["ruff"]["E402"],
                             {"tests/rules/ca/sl/test_demo.py": 2, "tests/x.py": 1})
            self.assertIn('"rules.ca.sl.test_demo", "test_other"',
                          (r / "scripts" / "mutation_audit.py").read_text())
            self.assertEqual((r / "scripts" / "ci_no_extras.sh").read_text(),
                             'EXTRAS_TESTS="rules.ca.sl.test_demo test_other"\n')
            self.assertEqual((r / ".github/workflows/tests.yml").read_text(),
                             "    run: python -m unittest rules.ca.sl.test_demo\n")
            self.assertEqual((r / "docs" / "map.md").read_text(),
                             "- `tests/rules/ca/sl/test_demo.py` — `TestX`\n")
            self.assertIn("ROUND_NAMED_MAX = 0\n", (r / LAYOUT).read_text())
            for hist in ("CHANGELOG.md", "backlog.md"):   # history, as it was
                self.assertIn("tests/test_fix_demo.py", (r / hist).read_text())
            self.assertEqual((r / "scripts" / "moved.py").read_text(),   # a tool (TJ-3707)
                             'x = "tests/test_fix_demo.py"\n')
            self.assertEqual(sorted(x.split(":")[0] for x in left),
                             ["CHANGELOG.md", "backlog.md", "docs/notes.md"])
            self.assertIn("docs/notes.md:1: see test_fix_demo for the demo", left)
            self.assertTrue(all("(history)" in x for x in left if not x.startswith("docs")))
            # git mv'd: the index holds the module, unchanged, at its new path.
            self.assertIn("R100\ttests/test_fix_demo.py\ttests/rules/ca/sl/test_demo.py\n",
                          _git(r, "diff", "--cached", "-M", "--name-status", base_tree))

            fmap = MV.formerly_map(r / "tests")
            self.assertEqual(MV.where("test_fix_demo", fmap, r / "tests"),
                             ["tests/rules/ca/sl/test_demo.py"])
            for name in ("test_fix_demo.TestX.test_a", "tests/test_fix_demo.py::TestX::test_a"):
                self.assertEqual(MV.where(name, fmap, r / "tests"),
                                 ["tests/rules/ca/sl/test_demo.py TestX.test_a"], name)
            self.assertEqual(MV.where("test_fix_demo.py", fmap, r / "tests"),
                             ["tests/rules/ca/sl/test_demo.py"])
            for gone in ("test_fix_other", "test_fix_demo.TestNo", "test_fix_demo.TestX.test_no"):
                self.assertEqual(MV.where(gone, fmap, r / "tests"), [], gone)

    def test_a_batch_it_cannot_do_whole_changes_nothing(self):
        bad_batches = {
            "does not exist": MOVE + [("test_fix_none", "rules/ca/sl/test_none.py")],
            "exists": [("test_fix_demo", "rules/ca/sl/test_demo.py"),
                       ("test_other", "rules/ca/sl/test_demo.py")],
            "no package": [("test_fix_demo", "rules/ca/sl/test_demo.py"),
                           ("test_other", "features/plain/test_other.py")],
            "not triple-quoted": MOVE + [("test_bad", "rules/ca/sl/test_bad.py")],
            "not tracked": MOVE + [("test_untracked", "rules/ca/sl/test_u.py")],   # TJ-3703
            "git ignores": MOVE + [("test_other", "rules/ca/inputs/test_other.py")],
            # ruff's default exclude: never linted (the B2 review)
            "ruff skips a folder named dist": MOVE + [("test_other", "rules/ca/dist/test_other.py")],
            "ruff skips a folder named node_modules":   # at any depth
                MOVE + [("test_other", "rules/node_modules/ca/test_other.py")],
            "named test_other already": [("test_fix_demo", "rules/ca/sl/test_other.py")],
            "the placement names two new files test_twice": [
                ("test_fix_demo", "rules/ca/sl/test_twice.py"),
                ("test_other", "features/misc/test_twice.py")],
            "named test_plugged already": [("test_fix_demo", "rules/ca/sl/test_plugged.py")],  # TJ-3750
            # its own name, which the plugin's tests have too (TJ-3753)
            "named test_shared already": [("test_shared", "rules/ca/sl/test_shared.py")],
        }
        for why, batch in bad_batches.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r)
                (r / "tests" / "features" / "plain").mkdir(parents=True)  # no __init__.py
                (r / "tests" / "test_bad.py").write_text('"one quote"\nx = 1\n')
                _git(r, "add", "tests/test_bad.py")
                (r / "tests" / "test_untracked.py").write_text('"""U."""\n')
                (r / "packages" / "p" / "tests").mkdir(parents=True)
                (r / "packages" / "p" / "tests" / "test_plugged.py").write_text('"""P."""\n')
                (r / "packages" / "p" / "tests" / "test_shared.py").write_text('"""P."""\n')
                (r / "tests" / "test_shared.py").write_text('"""S."""\n')
                _git(r, "add", "packages", "tests/test_shared.py")
                if why == "exists":
                    (r / "tests/rules/ca/sl").mkdir(parents=True)
                    for d in ("rules", "rules/ca", "rules/ca/sl"):
                        (r / "tests" / d / "__init__.py").write_text('"""p"""\n')
                    (r / "tests/rules/ca/sl/test_demo.py").write_text("x = 1\n")
                before = _git(r, "status", "--porcelain", "--untracked-files=all")
                with self.assertRaises(SystemExit) as cm:
                    MT.move(r, batch)
                self.assertIn(why, str(cm.exception))
                self.assertTrue((r / "tests" / "test_fix_demo.py").is_file())
                self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)

    def test_a_batch_that_fails_after_its_checks_is_undone(self):
        batch = MOVE + [("test_other", "features/misc/test_other.py")]
        real = os.replace

        def failing(src, dst):             # the write's last step (TJ-3725)
            if Path(dst).name == "test_other.py" and "misc" in Path(dst).parts:
                raise OSError("disk full")
            real(src, dst)
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r)
            before = _git(r, "status", "--porcelain", "--untracked-files=all")
            texts = {f: (r / f).read_bytes() for f in ("tests/test_fix_demo.py", "tests/test_other.py")}
            with mock.patch("os.replace", failing), \
                    self.assertRaises(SystemExit) as cm:
                MT.move(r, batch)
            self.assertIn("disk full", str(cm.exception))
            self.assertIn("undone", str(cm.exception))
            self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)
            for f, b in texts.items():
                self.assertEqual((r / f).read_bytes(), b, f)
            self.assertFalse((r / "tests" / "rules").exists())

    def test_a_write_is_all_or_nothing(self):
        # TJ-3725: a write that fails leaves the file as it was, and no
        # temporary file; one that succeeds keeps the file's mode.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "run.sh"
            p.write_text("old\n")
            os.chmod(p, 0o755)
            with mock.patch("os.replace", side_effect=OSError("disk full")), \
                    self.assertRaises(OSError):
                MT.write_exact(p, "new\n" * 1000)
            self.assertEqual((p.read_text(), sorted(q.name for q in Path(td).iterdir())),
                             ("old\n", ["run.sh"]))
            MT.write_exact(p, "new\r\n")
            self.assertEqual((p.read_bytes(), p.stat().st_mode & 0o777), (b"new\r\n", 0o755))
            seen = []
            real = os.replace
            with mock.patch("os.replace", side_effect=lambda a, b: (seen.append(Path(a)), real(a, b))):
                MT.write_exact(p, "again\n")
            self.assertEqual((seen[0].parent, seen[0].name), (p.parent, ".run.sh.move-tmp"))

    def test_line_ends_are_kept_through_a_move(self):
        # TJ-3688: read and written as they are, not as the platform's.
        crlf = b'"""Crlf."""\r\nimport unittest\r\n'
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r)
            (r / "tests" / "test_fix_crlf.py").write_bytes(crlf)
            _git(r, "add", "tests/test_fix_crlf.py")
            MT.move(r, [("test_fix_crlf", "rules/ca/sl/test_crlf.py")])
            self.assertEqual((r / "tests/rules/ca/sl/test_crlf.py").read_bytes(),
                             b'"""Crlf.\r\n\r\nFormerly: test_fix_crlf"""\r\nimport unittest\r\n')

    def test_its_own_name_another_module_has_is_refused(self):
        # TJ-3770: a moving module leaves `taken` by its path, so another
        # module of its name below tests/ still counts.
        extra = {"tests/test_dup.py": '"""Dup."""\n',
                 "tests/features/__init__.py": '"""Features."""\n',
                 "tests/features/kept/__init__.py": '"""Kept."""\n',
                 "tests/features/kept/test_dup.py": '"""Kept dup."""\n'}
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, extra)
            with self.assertRaises(SystemExit) as e:
                MT.move(r, [("test_dup", "rules/ca/sl/test_dup.py")])
            self.assertIn("a module below tests/ is named test_dup already", str(e.exception))

    def test_a_feature_area_is_one_feature_areas_names(self):
        # B11's review: a held row named features/views/, no area; the
        # mover refuses it, and a folder below an area, when the layout
        # module names the areas (a repository without them moves anywhere).
        areas = {LAYOUT: _LAYOUT_TEXT + 'FEATURE_AREAS = {"misc": ("Misc",)}\n'}
        for new, ok in (("features/misc/test_o.py", True), ("features/views/test_o.py", False),
                        ("features/misc/deep/test_o.py", False)):
            with tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, areas)
                self.assertEqual(MT.feature_areas(r), {"misc"})
                if ok:
                    MT.move(r, [("test_fix_demo", new)])
                    self.assertTrue((r / "tests" / new).is_file())
                    continue
                with self.assertRaises(SystemExit, msg=new) as e:
                    MT.move(r, [("test_fix_demo", new)])
                self.assertIn("an area of FEATURE_AREAS", str(e.exception))
                self.assertTrue((r / "tests/test_fix_demo.py").is_file())
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r)
            self.assertIsNone(MT.feature_areas(r))

    def test_a_module_a_script_runs_is_found_where_it_moved(self):
        # A RUN_FILES module moved names the others dotted where it is now (TJ-3707).
        extra = {RUN_MODULE: '"""Devtools."""\nRUN = ["test_fix_demo"]\n'}
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, extra)
            MT.move(r, MOVE + [(Path(RUN_MODULE).stem, "features/devtools/test_devtools.py")])
            self.assertIn('RUN = ["rules.ca.sl.test_demo"]',
                          (r / "tests/features/devtools/test_devtools.py").read_text())
            self.assertEqual(MT.moved_path(r / "tests", RUN_MODULE),
                             "tests/features/devtools/test_devtools.py")
            self.assertEqual(MT.moved_path(r / "tests", "tests/test_other.py"), "tests/test_other.py")

    def test_a_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r)
            before = _git(r, "status", "--porcelain", "--untracked-files=all")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(MT.move(r, MOVE, dry_run=True), [])
            self.assertEqual(out.getvalue(), "tests/test_fix_demo.py -> tests/rules/ca/sl/"
                                             "test_demo.py (rules.ca.sl.test_demo)\n")
            self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)

    def test_a_shared_helpers_source_line_is_history(self):
        # TJ-3996: kept as it is and not listed; every other mention listed.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, SHARED)
            left = MT.move(r, MOVE)
            self.assertEqual(sorted(x.split(": ", 1)[0] for x in left), [
                "CHANGELOG.md:1 (history)", "backlog.md:1 (history)", "docs/notes.md:1",
                "packages/p/tests/test_plugged.py:3", "tests/_support/demo_rows.py:6",
                "tests/_support/demo_rows.py:8", "tests/test_wire.py:3"])
            for rel, text in SHARED.items():
                self.assertEqual((r / rel).read_text(), text, rel)

    def test_the_verifier_renames_ids_as_the_mover_does(self):
        mods = {"test_fix_demo": "rules.ca.sl.test_demo"}
        classes = {"test_fix_big.TestY": "rules.us.wash.test_y"}
        self.assertEqual(VT.rename_id("test_fix_demo.TestX.test_a", mods, classes),
                         "rules.ca.sl.test_demo.TestX.test_a")
        self.assertEqual(VT.rename_id("test_fix_big.TestY.test_b", mods, classes),
                         "rules.us.wash.test_y.TestY.test_b")
        self.assertEqual(VT.rename_id("test_other.T.test_c", mods, classes), "test_other.T.test_c")


LAYOUT_STEM = Path(LAYOUT).stem


def _own(problems):
    """PROBLEMS but the earlier moves' check's (TJ-4211), which a case
    about another check's message sees too."""
    return [p for p in problems if "is named otherwise than at BASE" not in p]


class TestTheVerifiersTextCheck(unittest.TestCase):
    """verify_test_moves.text_problems after the mover's move, then one
    edit each: what a batch may carry (listed) and what it may not."""

    def check(self, edit=None, extra=None, moves=MOVE, add=True, mods=None, before=None,
              base_edit=None):
        """The text check from the base tree to the tree after the mover
        and EDIT (staged with `git add -A` when ADD); the base is after
        BEFORE's moves when given (an earlier batch, landed), then
        BASE_EDIT."""
        with tempfile.TemporaryDirectory() as td:
            r, base, head = Path(td) / "repo", Path(td) / "base", Path(td) / "head"
            for d in (r, base, head):
                d.mkdir()
            tree = self.base_tree = _synthetic(r, extra)
            if before:
                MT.move(r, before)
            if base_edit:
                base_edit(r)
            if before or base_edit:
                _git(r, "add", "-A")
                tree = self.base_tree = _git(r, "write-tree").strip()
            if moves:
                MT.move(r, moves)
            if edit:
                edit(r)
            if add:
                _git(r, "add", "-A")
            head_tree = _git(r, "write-tree").strip()
            VT.export(tree, base, r)
            VT.export(head_tree, head, r)
            got, _classes = VT.batch_moves(base, head)
            if mods is not False:      # False: whichever place batch_moves kept
                self.assertEqual(got, mods if mods is not None else
                                 {o: MT.dotted(n) for o, n in moves})
            problems, listed, _n = VT.text_problems(r, tree, head_tree, base, head, got)
            return problems, listed

    @staticmethod
    def append(rel, text):
        def edit(r):
            with open(r / rel, "a", encoding="utf-8") as f:
                f.write(text)
        return edit

    @staticmethod
    def replace(rel, old, new):
        def edit(r):
            p = r / rel
            p.write_text(p.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
        return edit

    def test_a_clean_move_passes(self):
        self.assertEqual(self.check(), ([], []))

    def test_a_shared_helpers_source_line_needs_no_rule(self):
        # TJ-3996: the line the mover keeps passes; one put as the new name
        # by hand is a docstring's edit, listed.
        self.assertEqual(self.check(extra=SHARED), ([], []))
        edit = self.replace("tests/_support/demo_rows.py", "from: test_fix_demo\n\"\"\"\nNOTE",
                            "from: rules.ca.sl.test_demo\n\"\"\"\nNOTE")
        self.assertEqual(self.check(edit, SHARED), ([], ["tests/_support/demo_rows.py"]))

    def test_what_a_batch_may_write_by_hand_is_listed(self):
        moved = "tests/rules/ca/sl/test_demo.py"
        for edit, f in ((self.replace(moved, "Demo tests.", "Demo tests, by subject."), moved),
                        (self.append("docs/notes.md", "more\n"), "docs/notes.md")):
            with self.subTest(f):
                self.assertEqual(self.check(edit), ([], [f]))

    def test_an_old_name_settled_by_hand_is_listed(self):
        # The mover lists the mention; the batch puts the new name (TJ-3706).
        extra = {"tests/test_wire.py": '"""Wire."""\nNAMES = ["test_fix_demo", "x"]\n'}
        for new, ok in (('"rules.ca.sl.test_demo"', True), ('"tests/rules/ca/sl/test_demo.py"', True),
                        ('"rules.ca.sl.test_other"', False), ('"test_demo"', False)):
            with self.subTest(new):
                problems, listed = self.check(
                    self.replace("tests/test_wire.py", '"test_fix_demo"', new), extra)
                self.assertEqual((problems, listed), ([], ["tests/test_wire.py"]) if ok
                                 else (problems, []))
                self.assertEqual(len(problems), 0 if ok else 1, problems)

    def test_a_module_a_script_runs_moves_with_the_others(self):
        extra = {RUN_MODULE: '"""Devtools."""\nRUN = ["test_fix_demo"]\n'}
        self.assertEqual(self.check(extra=extra, moves=MOVE + [
            (Path(RUN_MODULE).stem, "features/devtools/test_devtools.py")]), ([], []))

    def test_each_edit_a_move_must_not_carry_fails(self):
        moved = "tests/rules/ca/sl/test_demo.py"
        cases = {
            "code in a moved module": self.replace(moved, "pass", "self.fail()"),
            "code in a module that stayed": self.replace("tests/test_other.py", "pass", "return"),
            "a comment": self.append(moved, "# a note\n"),                         # TJ-3678
            "a coding line": self.replace(moved, '"""Demo', "# -*- coding: latin-1 -*-\n\"\"\"Demo"),
            "src": self.replace("src/x.py", "'CA'", "'US'"),                       # TJ-3676
            "a fixture": self.append("tests/fixtures/f.csv", "3,4\n"),
            "a wiring list": self.replace("scripts/ci_no_extras.sh", " test_other", ""),
            "a new file": self.append("src/y.py", "x = 1\n"),
            "a package's __init__.py": self.append("tests/rules/__init__.py",       # TJ-3677
                                                   "import unittest\n"),
            "a tool": self.append("scripts/moved.py", "y = 2\n"),                  # TJ-3679
            "a deletion": lambda r: (r / "docs" / "notes.md").unlink(),
            "the baseline": self.replace("scripts/architecture-baseline.json", '"tests/x.py": 1',
                                         '"tests/x.py": 0'),
            "a mode": lambda r: os.chmod(r / "scripts" / "ci_no_extras.sh", 0o755),   # TJ-3702
            "a symlink": self._symlink,
            "a package outside rules/ and features/":                               # TJ-3708
                lambda r: ((r / "tests" / "taxjson").mkdir(),
                           (r / "tests" / "taxjson" / "__init__.py").write_text('"""T."""\n')),
            "a copy, not a move": lambda r: (r / "tests" / "test_fix_demo.py").write_text(
                _git(r, "show", ":tests/rules/ca/sl/test_demo.py")),
            "an executable new __init__.py": lambda r: os.chmod(r / "tests/rules/__init__.py", 0o755),
            "not text": lambda r: (r / "tests" / "fixtures" / "f.csv").write_bytes(b"\xff\xfe\x00"),
            "a rename reverted": self.replace("docs/map.md", "tests/rules/ca/sl/test_demo.py",   # TJ-3732
                                              "tests/test_fix_demo.py"),
            "the ratchet reverted": self.replace(LAYOUT, "ROUND_NAMED_MAX = 0",
                                                 "ROUND_NAMED_MAX = 1"),
            "the baseline put back": lambda r: (r / "scripts/architecture-baseline.json").write_text(
                _git(r, "show", f"{self.base_tree}:scripts/architecture-baseline.json")),  # TJ-3747
        }
        for why, edit in cases.items():
            with self.subTest(why):
                problems, _listed = self.check(edit)
                if why == "a copy, not a move":     # the copy is also counted as round-named
                    self.assertIn("still there", problems[0])
                    continue
                self.assertEqual(len(problems), 1, problems)

    @staticmethod
    def _symlink(r):
        p = r / "tests" / "rules" / "ca" / "sl" / "test_demo.py"
        copy = r.parent / "copy.py"
        copy.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(copy)

    def test_a_module_moved_from_two_fails(self):
        # TJ-3745: a second `Formerly:` line passes a deleted module off as moved.
        def edit(r):
            self.replace("tests/rules/ca/sl/test_demo.py", "Formerly: test_fix_demo",
                         "Formerly: test_other\nFormerly: test_fix_demo")(r)
            (r / "tests" / "test_other.py").unlink()
        problems, _listed = self.check(edit, mods={"test_fix_demo": "rules.ca.sl.test_demo",
                                                   "test_other": "rules.ca.sl.test_demo"})
        self.assertIn("text: tests/rules/ca/sl/test_demo.py names 2 old modules (test_fix_demo, "
                      "test_other): a module moves from one", problems)

    def test_a_module_named_in_two_places_fails(self):
        # TJ-3751: batch_moves keeps one of the places, and a rename the
        # batch undid in the other's terms would pass.
        moves = MOVE + [("test_other", "features/misc/test_o.py")]
        for second in ("tests/features/misc/test_o.py", "tests/rules/ca/sl/test_demo.py"):
            line = ("Formerly: test_other" if second.endswith("test_o.py")
                    else "Formerly: test_fix_demo")
            old = "test_fix_demo" if second.endswith("test_o.py") else "test_other"
            with self.subTest(second):
                problems, _listed = self.check(self.replace(second, line,
                                                            f"Formerly: {old}\n{line}"),
                                               moves=moves, mods=False)
                self.assertIn(f"text: {old} is named whole by 2 `Formerly:` lines "
                              f"(features/misc/test_o.py, rules/ca/sl/test_demo.py): a module "
                              f"moves to one place", problems)

    def test_a_module_moved_before_named_in_two_places_fails(self):
        # TJ-3768: an earlier batch moved test_other, so BASE's tests/ has
        # it no more; a second whole line still hides an undone rename.
        problems, _listed = self.check(
            self.replace("tests/rules/ca/sl/test_demo.py", "Formerly: test_fix_demo",
                         "Formerly: test_fix_demo\nFormerly: test_other"),
            before=[("test_other", "features/misc/test_o.py")], mods=False)
        self.assertIn("text: test_other is named whole by 2 `Formerly:` lines "
                      "(features/misc/test_o.py, rules/ca/sl/test_demo.py): a module "
                      "moves to one place", problems)
        self.assertEqual(self.check(before=[("test_other", "features/misc/test_o.py")]),
                         ([], []))

    def test_a_line_claiming_an_earlier_move_fails(self):
        # TJ-4211: a batch's `Formerly:` lines name its own moves; a class
        # line claiming a module an earlier batch moved whole, or one no
        # batch moved, fails, as a second whole line does (TJ-3768), and
        # so does the earlier move's own line rewritten.
        before = [("test_other", "features/misc/test_o.py")]
        tail = ("): a batch's `Formerly:` lines name its own moves, and leave an earlier one's as "
                "they are")
        for old, how in (("test_other", "an earlier batch moved"), ("test_never", "no batch moved")):
            with self.subTest(old):
                problems, _listed = self.check(
                    self.replace("tests/rules/ca/sl/test_demo.py", "class TestX(unittest.TestCase):\n",
                                 f'class TestX(unittest.TestCase):\n    """Formerly: {old}.TestO"""\n'),
                    before=before)
                self.assertIn(f"text: {old}, which {how}, is named otherwise than at BASE "
                              f"({old}.TestO: nowhere at BASE, rules/ca/sl/test_demo.py (TestX) "
                              f"after{tail}", problems)
        problems, _listed = self.check(
            self.replace("tests/features/misc/test_o.py", "Formerly: test_other",
                         "Formerly: test_others"), before=before)
        self.assertIn("text: test_other, which an earlier batch moved, is named otherwise than at "
                      "BASE (test_other: features/misc/test_o.py at BASE, nowhere after" + tail,
                      problems)

    def test_a_line_said_twice_in_one_module_fails(self):
        # TJ-3778: moved_path counts the lines, so a second identical one
        # in the same docstring hides an undone rename too.
        problems, _listed = self.check(
            self.replace("tests/rules/ca/sl/test_demo.py", "Formerly: test_fix_demo",
                         "Formerly: test_fix_demo\nFormerly: test_fix_demo"), mods=False)
        self.assertIn("text: test_fix_demo is named whole by 2 `Formerly:` lines "
                      "(rules/ca/sl/test_demo.py, rules/ca/sl/test_demo.py): a module "
                      "moves to one place", problems)

    def test_a_moved_run_file_found_by_base(self):
        # TJ-3783: where a moved layout test or run file is comes from BASE,
        # so a batch that drops or rewrites its `Formerly:` line fails, and
        # the ratchet and the dotted rename still hold.
        lay = "tests/features/layout/test_layout.py"
        line = f"Formerly: {Path(LAYOUT).stem}"
        before = [(Path(LAYOUT).stem, "features/layout/test_layout.py")]
        self.assertEqual(self.check(before=before), ([], []))
        for why, new in (("dropped", ""), ("rewritten", f"{line} (whole)")):
            with self.subTest(why):
                problems, _listed = self.check(self.replace(lay, line, new), before=before)
                self.assertIn(f"text: {lay}: BASE and the batch's moves put it here; HEAD's "
                              f"`Formerly:` lines, at {LAYOUT}", problems)
                def both(r, new=new):
                    self.replace(lay, line, new)(r)
                    self.replace(lay, "ROUND_NAMED_MAX = 0", "ROUND_NAMED_MAX = 1")(r)
                problems, _listed = self.check(both, before=before)
                self.assertEqual(len(_own(problems)), 2, problems)
                said = (f"text: {LAYOUT_STEM}, which an earlier batch moved, is named otherwise "
                        f"than at BASE ({LAYOUT_STEM}: features/layout/test_layout.py at BASE, "
                        f"nowhere after)")
                self.assertTrue(any(p.startswith(said) for p in problems), problems)
        dev = "tests/features/devtools/test_devtools.py"
        extra = {RUN_MODULE: '"""Devtools."""\nRUN = ["test_fix_demo"]\n'}
        before = [(Path(RUN_MODULE).stem, "features/devtools/test_devtools.py")]
        self.assertEqual(self.check(extra=extra, before=before), ([], []))
        def undone(r):
            self.replace(dev, f"Formerly: {Path(RUN_MODULE).stem}", "")(r)
            self.replace(dev, '"rules.ca.sl.test_demo"', '"test_fix_demo"')(r)
        problems, _listed = self.check(undone, extra=extra, before=before)
        self.assertIn(f"text: {dev}: BASE and the batch's moves put it here; HEAD's "
                      f"`Formerly:` lines, at {RUN_MODULE}", problems)
        self.assertEqual(len(_own(problems)), 2, problems)
        said = (f"text: {Path(RUN_MODULE).stem}, which an earlier batch moved, is named otherwise "
                f"than at BASE ({Path(RUN_MODULE).stem}: features/devtools/test_devtools.py at "
                f"BASE, nowhere after)")
        self.assertTrue(any(p.startswith(said) for p in problems), problems)

    def test_a_batch_that_moves_the_layout_test_and_devtools_passes(self):
        # The batch itself moves them (TJ-3805), alone and with a module.
        extra = {RUN_MODULE: '"""Devtools."""\nRUN = ["test_fix_demo"]\n'}
        lay = (Path(LAYOUT).stem, "features/layout/test_layout.py")
        dev = (Path(RUN_MODULE).stem, "features/devtools/test_devtools.py")
        for moves in ([lay], [lay, dev], MOVE + [lay, dev]):
            with self.subTest(moves):
                self.assertEqual(self.check(extra=extra, moves=moves), ([], []))

    def test_a_base_whose_layout_line_was_dropped_fails(self):
        # TJ-3804: a commit, no batch, dropped the moved layout test's line.
        lay = "tests/features/layout/test_layout.py"
        before = [(Path(LAYOUT).stem, "features/layout/test_layout.py")]
        problems, _listed = self.check(
            before=before, mods=False,
            base_edit=self.replace(lay, f"Formerly: {Path(LAYOUT).stem}", ""))
        self.assertEqual(problems, [f"text: {LAYOUT}: BASE's `Formerly:` lines put the layout "
                                    f"test here, and BASE has no such file"])

    def test_class_and_dotted_lines_are_no_second_place(self):
        # TJ-3787: a class's line, and a dotted one (a class split), name no
        # module whole.
        moves = MOVE + [("test_other", "features/misc/test_o.py")]
        demo, o = "tests/rules/ca/sl/test_demo.py", "tests/features/misc/test_o.py"
        cases = {
            "a class line": self.replace(demo, "class TestX(unittest.TestCase):\n",
                                         'class TestX(unittest.TestCase):\n'
                                         '    """Formerly: test_other"""\n'),
            "a dotted line twice": lambda r: (
                self.replace(demo, "Formerly: test_fix_demo",
                             "Formerly: test_fix_demo\nFormerly: test_other.TestO")(r),
                self.replace(o, "Formerly: test_other",
                             "Formerly: test_other\nFormerly: test_other.TestO")(r)),
        }
        for why, edit in cases.items():
            with self.subTest(why):
                problems, _listed = self.check(edit, moves=moves, mods=False)
                self.assertEqual([p for p in problems if "named whole" in p], [])

    def test_what_the_unchanged_file_check_skips(self):
        # TJ-3747: a folder the mover leaves alone, a file not text.
        problems, _listed = self.check(extra={"audits/notes.md": "see tests/test_fix_demo.py\n",
                                              "tests/fixtures/b.dat": b"\xff test_fix_demo\n"})
        self.assertEqual(problems, [])

    def test_a_move_the_mover_would_refuse_fails(self):
        # Moved by hand: into no package root, onto a file BASE has, under
        # another module's name (TJ-3708, TJ-3726, TJ-3724).
        def by_hand(new, extra_new=None):
            def edit(r):
                text = MT.add_formerly((r / "tests/test_fix_demo.py").read_text(), "test_fix_demo")
                (r / "tests/test_fix_demo.py").unlink()
                for d in Path(new).parents:
                    (r / d).mkdir(parents=True, exist_ok=True)
                    if d.parts[:2] in (("tests", "rules"), ("tests", "features")) and \
                            not (r / d / "__init__.py").exists():
                        (r / d).mkdir(parents=True, exist_ok=True)
                        (r / d / "__init__.py").write_text('"""P."""\n')
                (r / new).write_text(text)
                (r / LAYOUT).write_text(
                    (r / LAYOUT).read_text().replace("ROUND_NAMED_MAX = 1",
                                                                          "ROUND_NAMED_MAX = 0"))
                for f in ("scripts/architecture-baseline.json", "scripts/mutation_audit.py",
                          "scripts/ci_no_extras.sh", ".github/workflows/tests.yml", "docs/map.md"):
                    (r / f).write_text(MT.rename_text(f, (r / f).read_text(),
                                                      {"test_fix_demo": new[len("tests/"):]})
                                       if f != "scripts/architecture-baseline.json" else
                                       json.dumps(MT.rekey_baseline(json.loads((r / f).read_text()),
                                                  {"tests/test_fix_demo.py": new}), indent=1) + "\n")
            return edit
        cases = {
            "tests/misc/test_demo.py": ("misc.test_demo", "is not a path to a test_*.py"),
            "tests/features/engine/test_cov.py": ("features.engine.test_cov",
                                                  "is in BASE: a module moves to a new path"),
            "tests/features/x/test_other.py": ("features.x.test_other",
                                               "another module below tests/ is named test_other"),
            # a name only the plugin's tests have (TJ-3752)
            "tests/features/x/test_plugged.py": ("features.x.test_plugged",
                                                 "another module below tests/ is named "
                                                 "test_plugged"),
        }
        extra = {"tests/features/__init__.py": '"""F."""\n',
                 "packages/p/tests/test_plugged.py": '"""P."""\n',
                 "tests/features/engine/__init__.py": '"""E."""\n',
                 "tests/features/engine/test_cov.py": '"""Cov."""\n'}
        for new, (dotted, why) in cases.items():
            with self.subTest(new):
                problems, _listed = self.check(by_hand(new), extra=extra, moves=[],
                                               mods={"test_fix_demo": dotted})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(why, problems[0])

    def test_a_staged_edit_the_work_tree_hides_fails(self):
        # The index carries the edit; the file is as the mover left it (TJ-3705).
        moved = "tests/rules/ca/sl/test_demo.py"

        def hidden(r):
            _git(r, "add", "-A")
            p = r / moved
            text = p.read_text()
            p.write_text(text.replace("pass", "self.fail()"))
            _git(r, "add", moved)
            p.write_text(text)
        problems, _listed = self.check(hidden, add=False)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(moved, problems[0])

    def test_judge_and_init(self):
        self.assertIsNone(VT.judge("tests/test_a.py", "x = 1\n", "x = 1\n"))
        self.assertEqual(VT.judge("tests/test_a.py", '"""A."""\nx = 1\n', '"""B."""\nx = 1\n'),
                         "listed")
        beyond = "differs from BASE's beyond the renames"
        self.assertEqual(VT.judge("tests/test_a.py", "x = 1\n", "x = 1  # n\n"),
                         f"text: tests/test_a.py: {beyond} and its docstrings")
        self.assertEqual(VT.judge("docs/a.md", "a\n", "b\n"), "listed")
        for f in ("tests/notes.md", "packages/p/tests/notes.md"):                # TJ-3715
            self.assertEqual(VT.judge(f, "a\n", "b\n"),
                             f"text: {f}: {beyond} (a file below tests/ is data)")
        olds = {"test_fix_a": "rules/ca/sl/test_b.py"}
        for new in ('"rules.ca.sl.test_b"', '"tests/rules/ca/sl/test_b.py"'):
            self.assertEqual(VT.judge("scripts/x.py", 'M = "test_fix_a"\n', f"M = {new}\n", olds),
                             "listed", new)
        for f, want, head in (
                ("src/taxjson/x.py", 'M = "test_fix_a"\n', 'M = "rules.ca.sl.test_b"\n'),   # src
                ("scripts/x.py", 'M = "test_fix_a"\n', 'M = "test_fix_b"\n'),       # not its name
                ("scripts/x.py", 'M = "test_fix_a"\n', 'M = "test_b"\n'),           # a bare stem
                ("scripts/x.py", 'M = "rules.ca.sl.test_b"\n', 'M = "test_fix_a"\n'),  # reversed
                ("scripts/x.py", 'M = "test_fix_a" + x\n', 'M = "rules.ca.sl.test_b" + y\n')):
            self.assertEqual(VT.judge(f, want, head, olds), f"text: {f}: {beyond}", (f, head))
        self.assertEqual(VT.judge("scripts/x.py", 'P = "test_fix_a.py"\n', 'P = "test_b.py"\n', olds),
                         "listed")
        for want, head in (('P = "packages/p/tests/test_fix_a.py"\n',     # another module's
                            'P = "packages/p/tests/test_b.py"\n'),
                           ('M = "test_fix_ab"\n', 'M = "rules.ca.sl.test_bb"\n'),  # a longer name
                           ('P = "test_fix_a.py"\n', 'P = "rules.ca.sl.test_b"\n')):  # a path as dotted
            self.assertEqual(VT.judge("scripts/x.py", want, head, olds),
                             f"text: scripts/x.py: {beyond}", head)
        self.assertEqual(VT.judge("src/a.py", '"""A."""\n', '"""B."""\n'), f"text: src/a.py: {beyond}")
        self.assertIsNone(VT.init_problem("tests/rules/__init__.py", '"""Rules."""\n'))
        more = "text: tests/rules/__init__.py: a new package's __init__.py holds more than a docstring"
        self.assertEqual(VT.init_problem("tests/rules/__init__.py", '"""Rules."""\nx = 1\n'), more)
        self.assertEqual(VT.init_problem("tests/rules/__init__.py", '"""Rules."""\n# x\n'), more)
        for f in ("tests/taxjson/__init__.py", "tests/fixtures/__init__.py",
                  "tests/features/test_x/__init__.py", "tests/rules/_x/__init__.py",
                  "packages/p/tests/rules/__init__.py"):
            self.assertEqual(VT.init_problem(f, '"""P."""\n'),
                             f"text: {f} is new, not a moved module or a package below "
                             f"tests/rules/ or tests/features/", f)

    def test_a_form_feed_moves_no_span(self):
        # str.splitlines would split at the form feed (TJ-3710).
        text = 'x = 1  # a\x0cb\ndef f():\n    """D."""\n    return x == 1\n'
        self.assertEqual(VT.without_docstrings(text),
                         'x = 1  # a\x0cb\ndef f():\n    \n    return x == 1\n')

    def test_only_docstrings_are_cut(self):
        text = ('"""M\u00e9."""\n# keep\nclass A:\n    """A."""\n    def f(self):\n'
                '        """F."""\n        return f"{1}"\n')
        self.assertEqual(VT.without_docstrings(text),
                         '\n# keep\nclass A:\n    \n    def f(self):\n        \n'
                         '        return f"{1}"\n')



class TestTheVerifiersRuns(unittest.TestCase):
    """The checks that read the runs (pins, wiring), and which copy runs."""

    def test_a_summary_that_differs_or_fails_is_a_problem(self):
        ok = subprocess.CompletedProcess([], 0, stdout="378 rules\n")
        self.assertEqual(VT.summary_problems(ok, ok), [])
        self.assertEqual(len(VT.summary_problems(ok, subprocess.CompletedProcess([], 0, "377\n"))), 1)
        bad = subprocess.CompletedProcess([], 1, stdout="378 rules\n")
        self.assertEqual(VT.summary_problems(ok, bad),
                         ["pins: check_tax_rules --summary fails in HEAD (exit 1)"])

    def test_a_stage_that_selects_otherwise_is_a_problem(self):
        mods = {"test_fix_demo": "rules.ca.sl.test_demo"}
        base = ({"test_fix_demo", "test_a"}, {"test_b"})
        self.assertEqual(VT.wiring_problems(base, ({"rules.ca.sl.test_demo", "test_a"}, {"test_b"}),
                                            mods), [])
        probs = VT.wiring_problems(base, ({"rules.ca.sl.test_demo"}, {"test_b", "test_c"}), mods)
        self.assertEqual([p.split(" stage")[0] for p in probs],
                         ["wiring: the no-extras", "wiring: the release-edits"])

    def test_the_copy_that_runs_is_bases(self):
        # TJ-3704, TJ-3722: BASE's copy runs, isolated, without PYTHONPATH,
        # whatever HEAD's copy says.
        def stub(rc):
            return ("import os, sys\nprint('copy', sys.flags.isolated, sys.flags.no_site, "
                    "'PYTHONPATH' in os.environ, "
                    "' '.join(a for a in sys.argv[1:] if a.startswith('--') "
                    f"and not a.startswith('--repo')))\nsys.exit({rc})  # --base-copy\n")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r, {"scripts/verify_test_moves.py": stub(7),
                                  "scripts/move_tests.py": "x = 1\n",
                                  "scripts/extract_support.py": "x = 1\n"})
            (r / "scripts" / "verify_test_moves.py").write_text(stub(9))
            _git(r, "add", "-A")
            head = _git(r, "write-tree").strip()
            res = subprocess.run([sys.executable, str(_paths.REPO / "scripts" / "verify_test_moves.py"),
                                  base, head, f"--repo={r}", "--trees", "--no-ids"],
                                 capture_output=True, text=True,
                                 env={**os.environ, "PYTHONPATH": str(r)})
            self.assertEqual((res.returncode, res.stdout),
                             (7, "copy 1 1 False --base-copy --trees --no-ids\n"), res.stderr)
            # A BASE whose verifier cannot run as BASE's copy is refused.
            (r / "scripts" / "verify_test_moves.py").write_text("print('old')\n")
            _git(r, "add", "-A")
            old = _git(r, "write-tree").strip()
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(VT.main([old, head, f"--repo={r}", "--trees"]), 2)
            self.assertIn("predates running as BASE's copy", err.getvalue())

    def test_a_repository_git_shows_otherwise_is_refused(self):
        # TJ-3723: replace refs make git show other objects.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r)
            a = _git(r, "rev-parse", ":src/x.py").strip()
            b = _git(r, "rev-parse", ":tests/test_other.py").strip()
            _git(r, "replace", a, b)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(VT.main([base, base, f"--repo={r}", "--trees"]), 2)
            self.assertIn("replace refs", err.getvalue())

    def test_grafts_are_refused_and_gits_variables_dropped(self):
        # TJ-3723, TJ-3747, TJ-3750.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r)
            (r / ".git" / "info").mkdir(exist_ok=True)
            (r / ".git" / "info" / "grafts").write_text("")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(VT.main([base, base, f"--repo={r}", "--trees"]), 2)
            self.assertIn("grafts", err.getvalue())
        with mock.patch.dict(os.environ, {"GIT_DIR": "/x", "GIT_OBJECT_DIRECTORY": "/y"}):
            env = VT._genv()
        self.assertEqual((env.get("GIT_NO_REPLACE_OBJECTS"), "GIT_DIR" in env,
                          "GIT_OBJECT_DIRECTORY" in env), ("1", False, False))

    def test_main_checks_the_ancestry_of_commits(self):
        # TJ-3747: main itself refuses a BASE that is not HEAD's ancestor.
        err = io.StringIO()
        with mock.patch.object(VT, "rewritten_history", return_value=None), \
                mock.patch.object(VT, "resolve", side_effect=lambda repo, rev: (rev, "commit")), \
                mock.patch.object(VT, "ancestry_problem", return_value="BASE b is not an ancestor"), \
                mock.patch.object(VT, "run_base_copy") as run, redirect_stderr(err):
            self.assertEqual(VT.main(["b", "h", "--repo=."]), 2)
        self.assertEqual((err.getvalue(), run.called), ("verify: BASE b is not an ancestor\n", False))

    def test_a_batch_that_fails_the_text_check_runs_nothing_of_heads(self):
        # TJ-3744, TJ-3749: the text problems printed, and no Python run.
        real = subprocess.run

        def run(cmd, *a, **k):
            if cmd[0] == sys.executable:
                raise AssertionError(f"HEAD's code ran: {cmd[:3]}")
            return real(cmd, *a, **k)
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r)
            MT.move(r, MOVE)
            p = r / "tests" / "rules" / "ca" / "sl" / "test_demo.py"
            p.write_text(p.read_text().replace("pass", "self.fail()", 1))
            (r / "src" / "x.py").write_text("COUNTRY = 'US'\n")      # a second problem
            _git(r, "add", "-A")
            head = _git(r, "write-tree").strip()
            out = io.StringIO()
            with mock.patch.object(VT.subprocess, "run", side_effect=run), \
                    mock.patch.object(VT, "_stage_sets", side_effect=AssertionError("wiring ran")), \
                    redirect_stdout(out):
                self.assertEqual(VT.verify(r, base, head, no_ids=False), 1)
        text = out.getvalue()
        self.assertIn("verify: text: tests/rules/ca/sl/test_demo.py: differs from BASE's", text)
        self.assertIn("verify: text: src/x.py", text)          # each problem printed (TJ-3755)
        self.assertTrue(text.endswith("verify: FAILED (the text check; nothing of HEAD's was run)\n"),
                        text)

    def test_base_must_be_an_ancestor(self):
        # Commits only (the synthetic repositories have none): this repository's.
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD~1"], cwd=_paths.REPO,
                           capture_output=True, text=True)
        if r.returncode:
            self.skipTest("no history (a shallow clone)")
        self.assertIsNone(VT.ancestry_problem(_paths.REPO, "HEAD~1", "HEAD"))
        self.assertIn("is not an ancestor", VT.ancestry_problem(_paths.REPO, "HEAD", "HEAD~1"))

    def test_trees_are_refused_without_trees(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(VT.main([base, base, f"--repo={r}"]), 2)
            self.assertIn("BASE is a tree, not a commit", err.getvalue())

    def test_moved_reads_a_backlog_reference(self):
        # TJ-3711: a line number and backticks.
        for name in ("tests/test_fix_x.py:120", "`tests/test_fix_x.py`", "test_fix_x.py:3:9",
                     "`test_fix_x.TestY`", "`tests/test_fix_x.py`:120",          # TJ-3730
                     "`tests/test_fix_x.py:120`",                                   # TJ-3746
                     "tests/test_fix_x.py:10-20", "`tests/test_fix_x.py:10-20`",    # TJ-3754
                     '"test_fix_x.py":12', "'tests/test_fix_x.py':12"):
            self.assertIn(MV.normalize(name), ("test_fix_x", "test_fix_x.TestY"), name)


# ---------------------------------------------------------------- splits
SPLIT_OLD = '''"""Split demo tests.

Three classes, two subjects."""
import json  # noqa: F401  (its import is the point)
import os
import unittest
from pathlib import Path, PurePath

_SEEN = []
_SEEN.append("loaded")
_STATE = {}


def setUpModule():
    _STATE["up"] = True


# ------------------------------------------------ helpers
def _row(n):
    # one row
    return {"n": n, "sep": os.sep}


_ONLY_A = PurePath("a")
_SQUARE = lambda n: n * n


class _Checks:
    def test_mixed(self):
        self.assertEqual(_row(1)["n"], 1)


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(".")


# A's tests
class TestA(_Checks, _Base):
    """A's tests."""

    def test_a(self):
        self.assertEqual(_ONLY_A.name, "a")
        self.assertTrue(_STATE["up"])
    # a trailing note on TestA


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertEqual(_SQUARE(3), 9)


class TestC(_Base):
    """C's tests,
    on two lines.
    """

    def test_c(self):
        self.assertEqual(_row(2)["n"], 2)
        self.assertEqual(_SEEN, ["loaded"])


if __name__ == "__main__":
    unittest.main()
# the end
'''
# What features/misc/test_split_c.py holds: OLD's lines but what TestC
# does not reach, the import cut to the name it reads, the two lines.
SPLIT_C = '''"""Split demo tests.

Three classes, two subjects.

Formerly: test_fix_split"""
import json  # noqa: F401  (its import is the point)
import os
import unittest
from pathlib import Path

_SEEN = []
_SEEN.append("loaded")
_STATE = {}


def setUpModule():
    _STATE["up"] = True


# ------------------------------------------------ helpers
def _row(n):
    # one row
    return {"n": n, "sep": os.sep}


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(".")


class TestC(_Base):
    """C's tests,
    on two lines.

    Formerly: test_fix_split.TestC
    """

    def test_c(self):
        self.assertEqual(_row(2)["n"], 2)
        self.assertEqual(_SEEN, ["loaded"])


if __name__ == "__main__":
    unittest.main()
# the end
'''
SPLIT_PLACE = [("test_fix_split::TestA", "rules/ca/sl/test_split_a.py"),
               ("test_fix_split::TestB", "rules/ca/sl/test_split_a.py"),
               ("test_fix_split::TestC", "features/misc/test_split_c.py")]
FRAG_A, FRAG_C = "tests/rules/ca/sl/test_split_a.py", "tests/features/misc/test_split_c.py"
SPLIT_EXTRA = {
    "tests/test_fix_split.py": SPLIT_OLD,
    "tests/_hermetic.py": '"""The runner\'s setup (a stub)."""\n',
    "scripts/check_architecture.py": (_paths.REPO / "scripts" / "check_architecture.py").read_text(
        encoding="utf-8"),
    "scripts/mutation_audit.py": 'TEST_CMDS = [["py", "-m", "unittest", "test_fix_demo", '
                                 '"test_fix_split.TestB"]]\n',
    "scripts/architecture-baseline.json": json.dumps(
        {"ruff": {"E402": {"tests/test_fix_demo.py": 2, "tests/x.py": 1},
                  "E731": {"tests/test_fix_split.py": 1},
                  "F401": {"tests/test_fix_split.py": 1}},
         "noqa": {"F401": {"tests/test_fix_split.py": 1}}}, indent=1) + "\n",
}


def _fake_ruff(root, rels):
    """ruff's hits as the tests stand it in (CI's suite has no ruff): E731
    on a module-level lambda, F401 on a line suppressing it."""
    out = {}
    for rel in rels:
        out[rel] = [(code, n) for n, ln in enumerate(MT.split_lines(MT.read_exact(root / rel)), 1)
                    for code, rx in (("E731", r"^\w+ = lambda"), ("F401", r"noqa: F401"))
                    if re.search(rx, ln)]
    return out


def _ruff(fake=_fake_ruff):
    """The fake ruff, in the mover and in the verifier's copy of it."""
    stack = mock.patch.object(MT, "ruff_hits", fake), mock.patch.object(VT.MT, "ruff_hits", fake)
    return _Both(*stack)


class _Both:
    def __init__(self, *cms):
        self.cms = cms

    def __enter__(self):
        for cm in self.cms:
            cm.__enter__()

    def __exit__(self, *exc):
        for cm in reversed(self.cms):
            cm.__exit__(*exc)


class _SplitCase(unittest.TestCase):
    def split(self, place=SPLIT_PLACE, extra=None, edit=None, fake=_fake_ruff):
        """The synthetic repository with tests/test_fix_split.py split by
        PLACE, then EDIT (staged): (repo, base tree, head tree, base
        export, head export), the left mentions on self.left."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        r, base, head = (Path(td.name) / n for n in ("repo", "base", "head"))
        for d in (r, base, head):
            d.mkdir()
        tree = _synthetic(r, {**SPLIT_EXTRA, **(extra or {})})
        with _ruff(fake), redirect_stdout(io.StringIO()):
            self.left = MT.move(r, list(place))
        self.status = _git(r, "status", "--porcelain", "--untracked-files=all")
        if edit:
            edit(r)
        _git(r, "add", "-A")
        head_tree = _git(r, "write-tree").strip()
        VT.export(tree, base, r)
        VT.export(head_tree, head, r)
        return r, tree, head_tree, base, head

    def verify_text(self, r, tree, head_tree, base, head):
        mods, _classes = VT.batch_moves(base, head)
        problems, listed, _n = VT.text_problems(r, tree, head_tree, base, head, mods)
        with _ruff():
            problems += VT.split_baseline_problems(base, head, VT.split_moves(base, head))
        return problems, listed


class TestTheSplit(_SplitCase):
    def test_a_split_on_a_synthetic_repository(self):
        r, _tree, _head, base, head = self.split()
        self.assertFalse((r / "tests" / "test_fix_split.py").exists())
        self.assertEqual((r / FRAG_C).read_text(), SPLIT_C)
        a = (r / FRAG_A).read_text()
        for line in ('\nclass _Checks:\n', '# A\'s tests\nclass TestA(_Checks, _Base):\n'
                     '    """A\'s tests.\n\n    Formerly: test_fix_split.TestA"""\n',
                     "        self.assertTrue(_STATE[\"up\"])\n    # a trailing note on TestA\n",
                     'class TestB(unittest.TestCase):\n    """Formerly: test_fix_split.TestB"""\n'
                     '    def test_b(self):\n', "from pathlib import Path, PurePath\n",
                     "_SQUARE = lambda n: n * n\n", "def setUpModule():\n"):
            self.assertIn(line, a)
        self.assertNotIn("class TestC", a)
        # In git: the module gone, the new files and packages added.
        for x in (f"A  {FRAG_A}", f"A  {FRAG_C}", "A  tests/features/misc/__init__.py"):
            self.assertIn(x, self.status)
        self.assertNotIn("test_fix_split.py", self.status)      # gone from the index too
        # The baseline: the module's counts gone, each file's ruff's (the
        # import both hold counts in each) and its own comments'.
        doc = json.loads((r / "scripts/architecture-baseline.json").read_text())
        self.assertEqual((doc["ruff"]["E731"], doc["ruff"]["F401"], doc["noqa"]["F401"]),
                         ({FRAG_A: 1}, {FRAG_A: 1, FRAG_C: 1}, {FRAG_A: 1, FRAG_C: 1}))
        self.assertEqual(doc["ruff"]["E402"], {"tests/test_fix_demo.py": 2, "tests/x.py": 1})
        # A script running a class by name follows it; the ratchet counts.
        self.assertIn('"rules.ca.sl.test_split_a.TestB"', (r / "scripts/mutation_audit.py").read_text())
        self.assertIn("ROUND_NAMED_MAX = 1\n", (r / LAYOUT).read_text())
        self.assertEqual([x for x in self.left if "test_split" in x], [])
        fmap = MV.formerly_map(r / "tests")
        self.assertEqual(MV.split_modules(fmap), {"test_fix_split": sorted(
            [FRAG_A[len("tests/"):], FRAG_C[len("tests/"):]])})
        self.assertEqual(MV.where("test_fix_split.TestC.test_c", fmap, r / "tests"),
                         [f"{FRAG_C} TestC.test_c"])
        self.assertEqual(sorted(MV.where("test_fix_split", fmap, r / "tests")), [FRAG_C, FRAG_A])
        self.assertEqual(formerly_problems(r / "tests"), [])
        # Every test id, renamed by its class's line, one to one.
        ids = {}
        for name, tree in (("base", base), ("head", head)):
            out = tree / "ids.json"
            subprocess.run([sys.executable, "-c", VT._COLLECT, str(out)], cwd=tree / "tests",
                           env=VT._env(tree, str(tree)), check=True, capture_output=True)
            got = json.loads(out.read_text())
            self.assertEqual(got["errors"], [])
            ids[name] = got["ids"]
        mods, classes = VT.batch_moves(base, head)
        self.assertEqual(sorted(VT.rename_id(t, mods, classes) for t in ids["base"]),
                         sorted(ids["head"]))
        self.assertIn("rules.ca.sl.test_split_a.TestA.test_mixed", ids["head"])
        # The new files run.
        run = subprocess.run([sys.executable, "-m", "unittest", "rules.ca.sl.test_split_a",
                              "features.misc.test_split_c"], cwd=head / "tests",
                             env=VT._env(head, str(head)), capture_output=True, text=True)
        self.assertIn("Ran 4 tests", run.stderr)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_line_ends_are_kept_through_a_split(self):
        crlf = SPLIT_OLD.replace("\n", "\r\n")
        r, *_rest = self.split(extra={"tests/test_fix_split.py": crlf.encode()})
        self.assertEqual((r / FRAG_C).read_bytes(), SPLIT_C.replace("\n", "\r\n").encode())
        self.assertNotIn(b"\n", (r / FRAG_A).read_bytes().replace(b"\r\n", b""))

    def test_a_dry_run_changes_nothing_and_says_what_is_copied(self):
        dead = SPLIT_OLD.replace("\n\nif __name__", "\n\ndef _dead():\n    return 0\n\n\nif __name__")
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_split.py": dead})
            before = _git(r, "status", "--porcelain", "--untracked-files=all")
            out = io.StringIO()
            with _ruff(), redirect_stdout(out):
                self.assertEqual(MT.move(r, SPLIT_PLACE, dry_run=True), [])
            self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)
        said = out.getvalue()
        self.assertIn(f"tests/test_fix_split.py :: TestC -> {FRAG_C} (features.misc.test_split_c)",
                      said)
        for x in ("copied into 2 files: line 19 (_row)", "copied into 2 files: line 33 (_Base)",
                  "copied into 2 files: line 11 (_STATE)", "in no new file: line 63 (_dead)"):
            self.assertIn(x, said)

    def test_what_a_split_cannot_do_is_refused_before_any_change(self):
        one = [("test_fix_split::TestA", "rules/ca/sl/test_split_a.py"),
               ("test_fix_split::TestB", "rules/ca/sl/test_split_a.py"),
               ("test_fix_split::TestC", "rules/ca/sl/test_split_a.py")]
        no_c = SPLIT_PLACE[:2]
        mixin = SPLIT_PLACE + [("test_fix_split::_Checks", "features/misc/test_split_c.py")]
        b_after = SPLIT_OLD.replace("class TestB(unittest.TestCase):", "class TestB(TestA):")

        def old(text):
            return {"tests/test_fix_split.py": text}
        cases = {
            "move it whole": (one, None),
            "TestC run(s) tests and no line places it": (no_c, None),
            "_Checks runs no tests here": (mixin, None),
            "no module-level class TestZ": (SPLIT_PLACE + [("test_fix_split::TestZ",
                                                            "features/misc/test_split_c.py")], None),
            "TestB's base TestA runs tests and goes to rules/ca/sl/test_split_a.py": (
                [SPLIT_PLACE[0], ("test_fix_split::TestB", "features/misc/test_b.py"),
                 SPLIT_PLACE[2]], old(b_after)),
            # a statement every file runs that reads a class one file takes
            "features/misc/test_split_c.py needs TestB (line 52, which every new file holds": (
                SPLIT_PLACE, old(SPLIT_OLD.replace("\n\nclass TestC", "\nTestB.maxDiff = None\n\n\n"
                                                    "class TestC"))),
            # a test changes a helper's state that two files would copy
            "test_c (line 58) changes _STATE after import": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                '        self.assertEqual(_SEEN, ["loaded"])\n',
                '        self.assertEqual(_SEEN, ["loaded"])\n        _STATE["c"] = 1\n'))),
            "changes module-level state when it is defined": (SPLIT_OLD and SPLIT_PLACE, old(
                SPLIT_OLD.replace("def _row(n):", "def _reg(f):\n    _SEEN.append(f)\n"
                                  "    return f\n\n\n@_reg\ndef _row(n):"))),
            "reads its own module's names by name (globals)": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                '    return {"n": n, "sep": os.sep}', '    return globals()["_STATE"]'))),
            "the test class TestD is bound in a if statement": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                'if __name__', 'if os.sep:\n    class TestD(unittest.TestCase):\n'
                '        def test_d(self):\n            pass\n\n\nif __name__'))),
            "line 9 holds two statements": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                '_SEEN = []\n', '_SEEN = []; _X = 1\n'))),
            "star-imports": (SPLIT_PLACE, old(SPLIT_OLD.replace("import os\n",
                                                                "import os\nfrom os.path import *\n"))),
            "an import with a comment inside it": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                "from pathlib import Path, PurePath\n",
                "from pathlib import (Path,  # the one TestC reads\n    PurePath)\n"))),
            "scripts/ci_no_extras.sh:1 names it as a module": (SPLIT_PLACE, {
                "scripts/ci_no_extras.sh": 'EXTRAS_TESTS="test_fix_demo test_fix_split"\n'}),
            "takes classes of test_fix_split and test_other": ([*SPLIT_PLACE, (
                "test_other::TestO", "features/misc/test_split_c.py")], None),
            "test_fix_split moves whole and by class": (
                [*SPLIT_PLACE, ("test_fix_split", "features/misc/test_s.py")], None),
            "TestB is bound at module level more than once": (SPLIT_PLACE, old(SPLIT_OLD.replace(
                "\n\nclass TestC", "\nTestB = TestB\n\n\nclass TestC"))),
            "scripts/ci_no_extras.sh:1 names its path": (SPLIT_PLACE, {
                "scripts/ci_no_extras.sh": 'python -m unittest tests/test_fix_split.py\n'}),
            "ruff does not run": (SPLIT_PLACE, None),
        }
        for why, (place, extra) in cases.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, {**SPLIT_EXTRA, **(extra or {})})
                before = _git(r, "status", "--porcelain", "--untracked-files=all")
                fake = (lambda root, rels: None) if why == "ruff does not run" else _fake_ruff
                with self.assertRaises(SystemExit) as cm, _ruff(fake), \
                        redirect_stdout(io.StringIO()):
                    MT.move(r, MT.read_placement("".join(f"{o} {n}\n" for o, n in place)))
                self.assertIn(why, str(cm.exception))
                self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)
                self.assertTrue((r / "tests" / "test_fix_split.py").is_file())

    def test_words_of_the_release_edits_stage_in_each_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_split.py": SPLIT_OLD.replace(
                "two subjects.", "two subjects, one CHANGELOG."),
                "scripts/ci.sh": "x=$(grep -rlE --include='test_*.py' 'CHANGELOG|pyproject' tests)\n"})
            self.assertEqual(MT.release_words(r), "CHANGELOG|pyproject")
            out = io.StringIO()
            with _ruff(), redirect_stdout(out):
                self.assertEqual(MT.move(r, SPLIT_PLACE, dry_run=True), [])
            self.assertNotIn("leaves", out.getvalue())
        # In one class's lines only: the other file leaves the stage, said.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_split.py": SPLIT_OLD.replace(
                "class TestB(", "# its CHANGELOG line\nclass TestB("),
                "scripts/ci.sh": "x=$(grep -rlE --include='test_*.py' 'CHANGELOG|pyproject' tests)\n",
                "scripts/ci_no_extras.sh": 'PLUGIN_RE="[\\"\']fetch[\\"\']|taxjson_fetch"\n'})
            self.assertEqual(MT.stage_patterns(r), {
                "no-extras": ("[\"']fetch[\"']|taxjson_fetch", True),
                "release-edits": ("CHANGELOG|pyproject", False)})
            out = io.StringIO()
            with _ruff(), redirect_stdout(out):
                self.assertEqual(MT.move(r, SPLIT_PLACE, dry_run=True), [])
            self.assertIn(f"  leaves the release-edits stage (its pattern is not in it; its tests "
                          f"run in the suite): {FRAG_C}\n", out.getvalue())
            self.assertNotIn("leaves the no-extras", out.getvalue())     # OLD not in it
        self.assertTrue(MT.stage_selects(("fetch", True), "x = 'fetch'\n"))
        self.assertFalse(MT.stage_selects(("fetch", True), "git fetch origin\n"))
        self.assertTrue(MT.stage_selects(("fetch", False), "git fetch origin\n"))

    def test_a_finding_of_a_new_files_own_is_undone(self):
        # ruff finds in a new file what the lines it took did not carry.
        def more(root, rels):
            out = _fake_ruff(root, rels)
            if FRAG_C in rels:
                out[FRAG_C] = out[FRAG_C] + [("F821", 1)]
            return out
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, SPLIT_EXTRA)
            before = _git(r, "status", "--porcelain", "--untracked-files=all")
            with self.assertRaises(SystemExit) as cm, _ruff(more), redirect_stdout(io.StringIO()):
                MT.move(r, SPLIT_PLACE, dry_run=True)        # the dry run checks it too
            self.assertIn(f"{FRAG_C}: ruff finds F821 1 time(s)", str(cm.exception))
            with self.assertRaises(SystemExit) as cm, _ruff(more), redirect_stdout(io.StringIO()):
                MT.move(r, SPLIT_PLACE)
            self.assertIn(f"{FRAG_C}: ruff finds F821 1 time(s)", str(cm.exception))
            self.assertIn("every change undone", str(cm.exception))
            self.assertEqual(_git(r, "status", "--porcelain", "--untracked-files=all"), before)
            self.assertEqual((r / "tests" / "test_fix_split.py").read_text(), SPLIT_OLD)
            self.assertFalse((r / "tests" / "features").exists())

    def test_a_split_file_without_its_class_line_fails_the_guard(self):
        r, *_rest = self.split(edit=lambda r: (r / FRAG_C).write_text(
            SPLIT_C.replace("\n    Formerly: test_fix_split.TestC\n", "\n")))
        self.assertEqual(formerly_problems(r / "tests"), [
            f"{FRAG_C[len('tests/'):]}: TestC runs tests and has no `Formerly: "
            f"test_fix_split.TestC` line"])


EVERYWHERE = '''"""Q."""
from __future__ import annotations
import os
import sys
import unittest

sys.path.insert(0, "x")
_CFG = {"a": 1}
_CFG["b"] = 2
_UNUSED = os.getcwd()
_K = 1
_K = _K + 1
_FLAG = _CFG.setdefault("f", 1)


def _env():
    os.environ.setdefault("Q_SPLIT", "1")
    return 1


_MADE = _env()


def tearDownModule():
    pass


class TestX(unittest.TestCase):
    def test_x(self):
        self.assertEqual((_K, _FLAG, _MADE), (2, 1, 1))


class TestY(unittest.TestCase):
    def test_y(self):
        pass
'''


class TestTheSplitsPieces(unittest.TestCase):
    def test_what_runs_at_import_goes_into_every_file(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tests").mkdir()
            (Path(td) / "tests" / "test_fix_q.py").write_text(EVERYWHERE)
            plan = MT.SplitPlan(Path(td), "test_fix_q", [("TestX", "rules/ca/sl/test_q_x.py"),
                                                         ("TestY", "rules/ca/sl/test_q_y.py")])
        stmts = plan.lay.stmts
        self.assertEqual({stmts[i].lineno: why.split(" (")[0] for i, why in plan.everywhere.items()},
                         {2: "a __future__ import", 7: "runs code at import", 9: "a store at import",
                          10: "a call no statement reads the result of", 13: "changes at import",
                          21: "changes at import", 24: "a module hook"})
        self.assertIn("_env(): ", plan.everywhere[next(i for i, s in enumerate(stmts)
                                                       if s.lineno == 21)])
        lines = {new: sorted(stmts[i].lineno for i in keep) for new, keep in plan.keeps.items()}
        self.assertEqual(lines["rules/ca/sl/test_q_x.py"],
                         [2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 16, 21, 24, 28])
        self.assertEqual(lines["rules/ca/sl/test_q_y.py"], [2, 3, 4, 5, 7, 8, 9, 10, 13, 16, 21, 24, 33])

    def test_every_line_is_in_one_span(self):
        lay = MT.Layout(SPLIT_OLD)
        lines = [lay.head] + [b - a + 1 for a, b in lay.spans] + [len(lay.lines) - lay.foot + 1]
        self.assertEqual(sum(lines), len(MT.split_lines(SPLIT_OLD)))
        test_a = next(i for i, s in enumerate(lay.stmts) if getattr(s, "name", "") == "TestA")
        self.assertEqual(lay.stmt_lines(test_a)[:3], ["\n", "\n", "# A's tests\n"])
        self.assertEqual(lay.stmt_lines(test_a)[-1], "    # a trailing note on TestA\n")
        with self.assertRaises(ValueError):
            MT.Layout('"""D."""; x = 1\n')

    def test_a_class_line_ends_its_docstring(self):
        line = "Formerly: test_fix_a.TestX"
        for before, after in (
                ('class TestX:\n    """One."""\n', 'class TestX:\n    """One.\n\n    {}"""\n'),
                ('class TestX:\n    """Two\n    lines.\n    """\n',
                 'class TestX:\n    """Two\n    lines.\n\n    {}\n    """\n'),
                ("class TestX:\n    x = 1\n", 'class TestX:\n    """{}"""\n    x = 1\n'),
                ("class TestX:\n    # c\n    @staticmethod\n    def f():\n        pass\n",
                 'class TestX:\n    """{}"""\n    # c\n    @staticmethod\n    def f():\n        pass\n'),
                # right below the header, a blank line above `def` kept below it
                ("class TestX(\n        A,\n        B):\n\n    def f(self):\n        pass\n",
                 'class TestX(\n        A,\n        B):\n    """{}"""\n\n    def f(self):\n        pass\n'),
                ('class TestX:\r\n    """One."""\r\n', 'class TestX:\r\n    """One.\r\n\r\n    {}"""\r\n')):
            self.assertEqual(MT.add_class_formerly(before, "TestX", line), after.format(line))
        for bad in ("class TestX: pass\n", "class TestX:\n    'one quote'\n"):
            with self.assertRaises(ValueError, msg=bad):
                MT.add_class_formerly(bad, "TestX", line)

    def test_a_group_keeps_its_blank_line_when_its_first_import_goes(self):
        text = ('"""D."""\n\nimport os\nimport sys\n\nfrom a import x\nfrom b import y\n\n\n'
                'def f():\n    pass\n')
        self.assertEqual(MT.fragment_text(text, "test_fix_a", {0: None, 1: None, 3: None, 4: None},
                                          []),
                         '"""D.\n\nFormerly: test_fix_a"""\n\nimport os\nimport sys\n\n'
                         'from b import y\n\n\ndef f():\n    pass\n')
        self.assertEqual(MT.fragment_text(text, "test_fix_a", {2: None, 4: None}, []),
                         '"""D.\n\nFormerly: test_fix_a"""\n\nfrom a import x\n\n\n'
                         'def f():\n    pass\n')
        # A run left out: its first statement's blank lines, once.
        text = 'import os\n\nfrom a import x\nfrom c import z\nfrom b import y\nimport re\n'
        self.assertEqual(MT.fragment_text(text, "test_fix_a", {0: None, 3: None, 4: None}, []),
                         '"""Formerly: test_fix_a"""\nimport os\n\nfrom b import y\nimport re\n')

    def test_an_import_keeps_the_names_a_file_reads(self):
        text = "import os, sys\nfrom a.b import (x as y,\n    z)  # noqa: F401\nw = 1\n"
        self.assertEqual(MT.fragment_text(text, "test_fix_a", {0: ("sys",), 1: ("z",)}, []),
                         '"""Formerly: test_fix_a"""\nimport sys\nfrom a.b import z  # noqa: F401\n')
        long = [f"name_{i}" for i in range(12)]
        s = ast.parse(f"from pkg.mod import {', '.join(long)}\n").body[0]
        self.assertEqual(MT.import_text(s, s.names[:11]).splitlines()[0], "from pkg.mod import (")
        with self.assertRaises(ValueError):
            MT.fragment_text(text, "test_fix_a", {0: ("re",)}, [])

    def test_a_placement_line_names_a_class(self):
        self.assertEqual(MT.read_placement("test_fix_a::TestX rules/ca/sl/test_b.py\n"
                                           "test_fix_a::TestY rules/ca/sl/test_b.py\n"),
                         [("test_fix_a::TestX", "rules/ca/sl/test_b.py"),
                          ("test_fix_a::TestY", "rules/ca/sl/test_b.py")])
        for bad in ("test_fix_a::TestX rules/ca/sl/test_b.py\ntest_fix_a::TestX rules/us/x/test_c.py",
                    "test_fix_a::TestX rules/ca/sl/test_b.py\ntest_fix_c rules/ca/sl/test_b.py",
                    "test_fix_c rules/ca/sl/test_b.py\ntest_fix_a::TestX rules/ca/sl/test_b.py",
                    "test_fix_a::Test-X rules/ca/sl/test_b.py"):
            with self.assertRaises(SystemExit, msg=bad):
                MT.read_placement(bad)

    def test_a_script_follows_a_class(self):
        classes = {"test_fix_a.TestX": "rules/ca/sl/test_b.py"}
        self.assertEqual(MT.rename_text("scripts/mutation_audit.py",
                                        '"test_fix_a.TestX", "test_fix_a.TestXY.t"', {},
                                        classes=classes),
                         '"rules.ca.sl.test_b.TestX", "test_fix_a.TestXY.t"')

    def test_a_pin_is_in_each_copy(self):
        held = {"test_fix_a": {"_Mixin": ["x.test_b", "y.test_c"]}}
        classes = {"test_fix_a.TestX": "x.test_b"}
        self.assertEqual(VT.pin_keys("test_fix_a._Mixin.test_m", {}, classes, held),
                         ["x.test_b._Mixin.test_m", "y.test_c._Mixin.test_m"])
        self.assertEqual(VT.pin_keys("test_fix_a.TestX.test_x", {}, classes, held),
                         ["x.test_b.TestX.test_x"])
        self.assertEqual(VT.pin_keys("test_other.T.t", {}, classes, held), ["test_other.T.t"])

    def test_a_split_class_is_a_test_class(self):
        tree = ast.parse("import unittest\nclass M:\n    def test_m(self): pass\n"
                         "class B(unittest.TestCase):\n    def setUp(self): pass\n"
                         "class TestA(M, B):\n    pass\nclass TestE(M):\n    pass\n"
                         "class TestF(object):\n    def test_f(self): pass\n"
                         "class TestG(unittest.TestCase):\n    def runTest(self): pass\n"
                         "class TestH(B):\n    test_h = M.test_m\n"
                         "class TestI(fixture.Base):\n    def test_i(self): pass\n"
                         "class TestJ(other.Base, unittest.TestCase):\n    pass\n")
        self.assertEqual(MV.test_classes(tree), ["TestA", "TestG", "TestH", "TestI", "TestJ"])
        ES = MT._sibling("extract_support")
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        self.assertEqual([c for c in classes if ES.Plan._runs_tests(classes[c], classes)],
                         MV.test_classes(tree))       # as the mover reads them

    def test_ids_name_a_lost_or_doubled_duplicate(self):
        self.assertIsNone(VT.ids_problem(["a", "b", "a"], ["a", "a", "b"]))
        self.assertEqual(VT.ids_problem(["a", "a", "b"], ["a", "b"]),
                         "ids: 1 of BASE's (renamed) missing, 0 new: ['a']")
        self.assertEqual(VT.ids_problem(["a"], ["a", "a", "a", "c"]),
                         "ids: 0 of BASE's (renamed) missing, 3 new: ['a (x2)', 'c']")

    def test_what_a_copy_of_a_class_holds_apart(self):
        def state(src):
            return MT.class_state(ast.parse(src).body[0])
        self.assertIsNone(state("class A:\n    x = 1\n    y = (1, 'a')\n    z = frozenset({1})\n"
                                "    w = Path('a')\n    maxDiff = None\n    v = OTHER\n"))
        for src, why in (("class A:\n    seen = []\n", "a class attribute that is a list (seen"),
                         ("class A:\n    rows = make()\n", "make()'s value (rows"),
                         ("class A:\n    t = (1, {})\n", "a dict (t"),
                         ("class A(B, metaclass=M):\n    pass\n", "a metaclass"),
                         ("class A:\n    def __init_subclass__(cls):\n        pass\n",
                          "__init_subclass__")):
            self.assertIn(why, state(src) or "", src)
        # Through the module: an attribute nothing changes holds no state apart.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            (r / "tests").mkdir()
            for name, src in (("unchanged", REVIEW["unchanged"]), ("mixin", REVIEW["mixin"])):
                (r / "tests" / f"test_fix_zz_{name}.py").write_text(src)
            plan = MT.SplitPlan(r, "test_fix_zz_unchanged", _places("unchanged"))
            self.assertEqual([plan.lay.stmts[i].lineno for i in plan.copied], [9])  # _Run
            for edit, line in (("        cls.DATES.update(b=2)\n", 16),
                               ("        cls.DATES[\"b\"] = 2\n", 16),
                               ("        cls.ROWS += [1]\n", 16),
                               ("        _fill(cls.DATES)\n", 16),
                               # any code: a second name, a call of another module's
                               ("        rows = cls.ROWS\n", 16),
                               ("        bisect.insort(cls.ROWS, 1)\n", 16)):
                with self.subTest(edit):
                    (r / "tests" / "test_fix_zz_unchanged.py").write_text(REVIEW["unchanged"].replace(
                        "        cls.n = len(cls.DATES)\n", "        cls.n = len(cls.DATES)\n" + edit))
                    with self.assertRaises(SystemExit) as cm:
                        MT.SplitPlan(r, "test_fix_zz_unchanged", _places("unchanged"))
                    want = "a list (ROWS" if "ROWS" in edit else "dict()'s value (DATES"
                    self.assertIn(f"_Run (line 9) goes into 2 new files and has a class attribute "
                                  f"that is {want}", str(cm.exception))
                    self.assertIn(f" at line {line}", str(cm.exception))


class TestTheVerifierOnASplit(_SplitCase):
    def test_a_split_passes_and_one_tampered_fails(self):
        r, tree, head_tree, base, head = self.split()
        self.assertEqual(self.verify_text(r, tree, head_tree, base, head), ([], []))
        splits = VT.split_moves(base, head)
        self.assertEqual(VT.split_moves(head, head), {})        # a later batch's base
        plans, refused = VT.split_plans(base, head, splits)
        self.assertEqual(refused, [])
        self.assertEqual(plans["test_fix_split"].texts[FRAG_C[len("tests/"):]], SPLIT_C)
        self.assertEqual(VT.split_file_problems(FRAG_C, "M", "100644", lambda: SPLIT_C,
                                                plans["test_fix_split"], {}),
                         ([f"text: {FRAG_C} is in BASE: a split writes new files"], False))

        def binary():
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "x")
        self.assertEqual(VT.split_file_problems(FRAG_C, "A", "100644", binary,
                                                plans["test_fix_split"], {}),
                         ([f"text: {FRAG_C}: not text"], False))
        held = VT.held_names(plans)["test_fix_split"]
        a, c = "rules.ca.sl.test_split_a", "features.misc.test_split_c"
        self.assertEqual({k: sorted(held[k]) for k in ("_Base", "_Checks", "_row", "TestC")},
                         {"_Base": [c, a], "_Checks": [a], "_row": [c, a], "TestC": [c]})

        def change(f, old, new):
            return lambda r: (r / f).write_text((r / f).read_text().replace(old, new, 1))

        def back(f):
            return lambda r: (r / f).write_text(SPLIT_EXTRA[f])

        def count(kind, code, f, n):
            def edit(r):
                p = r / "scripts/architecture-baseline.json"
                doc = json.loads(p.read_text())
                doc[kind].setdefault(code, {})[f] = n
                p.write_text(json.dumps(doc, indent=1) + "\n")
            return edit
        doc_edit = change(FRAG_C, "C's tests,", "C's tests, by subject,")
        self.assertEqual(self.verify_text(*self.split(edit=doc_edit)), ([], [FRAG_C]))
        moved_up = SPLIT_C.replace("import os\nimport unittest\n", "import unittest\nimport os\n")
        test_b = ('class TestB(unittest.TestCase):\n    def test_b(self):\n'
                  '        self.assertEqual(_SQUARE(3), 9)\n')
        cut_c = f"text: {FRAG_C}: not BASE's mover's cut of tests/test_fix_split.py: line"
        refused = "text: BASE's mover refuses the split HEAD's class lines make: "
        cases = {
            "a line changed": (change(FRAG_C, '["n"], 2)', '["n"], 3)'), cut_c),
            "a comment dropped": (change(FRAG_C, "    # one row\n", ""), cut_c),
            "a line moved": (lambda r: (r / FRAG_C).write_text(moved_up), cut_c),
            "a call at import dropped": (change(FRAG_C, '_SEEN.append("loaded")\n', ""), cut_c),
            "a constant changed in a copy": (change(FRAG_C, '"sep": os.sep', '"sep": "/"'), cut_c),
            "a helper dropped": (change(FRAG_C, 'def _row(n):\n    # one row\n    return '
                                        '{"n": n, "sep": os.sep}\n\n\n', ""), cut_c),
            "a class dropped": (change(FRAG_A, "class TestB(unittest.TestCase):\n    \"\"\"Formerly: "
                                       "test_fix_split.TestB\"\"\"\n    def test_b(self):\n        "
                                       "self.assertEqual(_SQUARE(3), 9)\n\n\n", ""),
                                refused + "tests/test_fix_split.py: TestB run(s) tests and no line "
                                          "places it"),
            "a class added out of order": (change(FRAG_C, "\n\nif __name__", "\n\n" + test_b
                                                  + "\n\nif __name__"), cut_c),
            "a class copied": (change(FRAG_C, "\n\nclass TestC", "\n\n" + test_b + "\n\nclass TestC"),
                               cut_c),
            "a class copied with its line": (
                change(FRAG_C, "\n\nclass TestC", "\n\n" + test_b.replace(
                    "TestCase):\n", 'TestCase):\n    """Formerly: test_fix_split.TestB"""\n')
                       + "\n\nclass TestC"),
                "text: test_fix_split.TestB is named by 2 class lines"),
            "a class line dropped": (change(FRAG_C, "\n\n    Formerly: test_fix_split.TestC\n", "\n"),
                                     refused + "tests/test_fix_split.py: TestC run(s) tests and no "
                                               "line places it"),
            "a class line naming another module": (
                change(FRAG_C, "Formerly: test_fix_split.TestC", "Formerly: test_fix_split.TestC\n"
                       "    Formerly: test_other.TestC"),
                f"text: {FRAG_C}: TestC's line names test_other.TestC"),
            "a mode": (lambda r: os.chmod(r / FRAG_C, 0o755),
                       f"text: {FRAG_C}: a split's new file, not a plain one"),
            "the baseline BASE's": (back("scripts/architecture-baseline.json"),
                                    "text: scripts/architecture-baseline.json is BASE's"),
            "a script's class left": (back("scripts/mutation_audit.py"),
                                      "text: scripts/mutation_audit.py is BASE's, but the moves"),
            "a hook missing": (change(FRAG_C, '\n\ndef setUpModule():\n    _STATE["up"] = True\n', ""),
                               cut_c),
            "the module kept": (lambda r: (r / "tests/test_fix_split.py").write_text(SPLIT_OLD),
                                "text: tests/test_fix_split.py is still there"),
            "a second module named": (change(FRAG_C, "Formerly: test_fix_split\"\"\"",
                                             "Formerly: test_fix_split\nFormerly: test_other\"\"\""),
                                      f"text: {FRAG_C}: its docstring names"),
            "a ruff count over its lines'": (count("ruff", "E731", FRAG_C, 1),
                                             f"baseline: {FRAG_C}: ruff E731 1, more than the 0"),
            "a noqa count not its comments'": (count("noqa", "F401", FRAG_C, 2),
                                               f"baseline: {FRAG_C}: its noqa counts"),
            "the module's count kept": (count("ruff", "E731", "tests/test_fix_split.py", 1),
                                        "baseline: HEAD still counts ['tests/test_fix_split.py']"),
        }
        for why, (edit, want) in cases.items():
            with self.subTest(why):
                problems, _listed = self.verify_text(*self.split(edit=edit))
                self.assertTrue(any(p.startswith(want) for p in problems), problems)

    def test_a_split_the_mover_refuses_made_by_hand_fails(self):
        # The mover's own refusals switched off, as a person cutting by hand.
        with mock.patch.object(MT, "run_file_problems", lambda *a: []):
            got = self.split(extra={"scripts/ci_no_extras.sh":
                                    'EXTRAS_TESTS="test_fix_demo test_fix_split"\n'})
        problems, _listed = self.verify_text(*got)
        self.assertIn("text: BASE's mover refuses the split of tests/test_fix_split.py: "
                      "scripts/ci_no_extras.sh:1 names it as a module", "\n".join(problems))
        extra = "tests/features/misc/test_split_x.py"
        got = self.split(edit=lambda r: (r / extra).write_text('"""X.\n\nFormerly: test_fix_split"""\n'))
        problems, _listed = self.verify_text(*got)
        self.assertIn(f"text: {extra} names test_fix_split and takes none of its classes", problems)
        for name, off in (("argmut", ("_shared_state",)), ("r1", ("_effect", "_shared_state")),
                          ("own", ("_own_imports",))):
            with self.subTest(name), contextlib.ExitStack() as stack:
                for target in off:
                    stack.enter_context(mock.patch.object(MT.SplitPlan, target, lambda *a: None))
                got = self.split(place=_ab(name), extra=_module(name))
                problems, _listed = self.verify_text(*got)
                self.assertTrue(any(p.startswith(
                    "text: BASE's mover refuses the split HEAD's class lines make: tests/"
                    f"test_fix_zz_{name}.py: ") for p in problems), problems)

    def test_the_wiring_follows_a_split(self):
        splits = {"test_fix_split": ["x/test_y.py", "x/test_z.py"]}
        classes = {"test_fix_split.TestA": "x.test_y"}
        # By class, the class follows its line.
        base = ({"test_fix_split.TestA", "test_a"}, {"test_b"})
        self.assertEqual(VT.wiring_problems(base, ({"x.test_y.TestA", "test_a"}, {"test_b"}), {},
                                            splits, classes), [])
        # A stage's grep picks each file by its own text; one it no longer
        # picks leaves the stage, listed.
        base = ({"test_fix_split", "test_a"}, {"test_fix_split", "test_b"})
        head = ({"x.test_y", "test_a"}, {"x.test_y", "x.test_z", "test_b"})
        picks = {"no-extras": {"x.test_y"}, "release-edits": {"x.test_y", "x.test_z"}}
        self.assertEqual(VT.wiring_problems(base, head, {}, splits, classes, picks), [])
        self.assertEqual(VT.stage_leavers(base, head, splits), ["no-extras: x.test_z"])
        probs = VT.wiring_problems(base, ({"test_a"}, {"x.test_y", "test_b"}), {}, splits, classes,
                                   picks)
        self.assertEqual(probs, ["wiring: the no-extras stage selects ['x.test_y'] differently",
                                 "wiring: the release-edits stage selects ['x.test_z'] differently"])
        # The picks: each new file by its own text, with BASE's patterns.
        ci = {"scripts/ci.sh": "x=$(grep -rlE --include='test_*.py' 'CHANGELOG|pyproject' tests)\n",
              "tests/test_fix_split.py": SPLIT_OLD.replace("class TestC(", "# CHANGELOG\nclass TestC(")}
        _r, _t, _h, b, h = self.split(extra=ci)
        sp = VT.split_moves(b, h)
        self.assertEqual(VT.stage_picks(b, h, sp), {"release-edits": {"features.misc.test_split_c"}})
        self.assertEqual(VT.stage_leavers((set(), {"test_fix_split"}),
                                          (set(), {"features.misc.test_split_c"}), sp),
                         ["release-edits: rules.ca.sl.test_split_a"])
        # A file its module's stage did not pick, picked, is a problem.
        probs = VT.wiring_problems(({"test_a"}, set()), ({"test_a", "x.test_y"}, set()), {},
                                   splits, classes, picks)
        self.assertEqual(probs, ["wiring: the no-extras stage selects ['x.test_y'] differently"])


# The reviews' cases (2026-11-16): each a module of two test classes,
# TestAlpha and TestBeta, split one to a file (_ab).
REVIEW = {
    "argmut": '''"""A list changed through a helper's argument."""
import unittest

_LOG = []


def _record(log, item):
    log.append(item)
    return len(log)


class TestAlpha(unittest.TestCase):
    def test_first(self):
        self.assertEqual(_record(_LOG, "alpha"), 1)


class TestBeta(unittest.TestCase):
    def test_second(self):
        self.assertEqual(_record(_LOG, "beta"), 2)
''',
    "box": '''"""An object changed by its own method."""
import unittest


class _Counter:
    def __init__(self):
        self.n = 0

    def bump(self):
        self.n += 1
        return self.n


_COUNTER = _Counter()


class TestAlpha(unittest.TestCase):
    def test_bump(self):
        self.assertGreaterEqual(_COUNTER.bump(), 1)


class TestBeta(unittest.TestCase):
    def test_after_alpha(self):
        self.assertEqual(_COUNTER.bump(), 2)
''',
    "mixin": '''"""A mixin's class attribute two classes share."""
import unittest


class _Seen:
    seen = []

    def note(self):
        self.seen.append(type(self).__name__)
        return len(self.seen)


class TestAlpha(_Seen, unittest.TestCase):
    def test_first(self):
        self.assertGreaterEqual(self.note(), 1)


class TestBeta(_Seen, unittest.TestCase):
    def test_after_alpha(self):
        self.assertEqual(self.note(), 2)
''',
    "subclass": '''"""A base that registers each class built on it."""
import unittest


class _Registered(unittest.TestCase):
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls.registry.append(cls.__name__)


class TestAlpha(_Registered):
    def test_registered(self):
        self.assertIn("TestAlpha", self.registry)


class TestBeta(_Registered):
    def test_all(self):
        self.assertEqual(len(self.registry), 2)
''',
    "r1": '''"""A decorator that registers each class."""
import unittest

_REGISTRY = []


def _register(cls):
    _REGISTRY.append(cls.__name__)
    return cls


@_register
class TestAlpha(unittest.TestCase):
    def test_registered(self):
        self.assertIn("TestAlpha", _REGISTRY)


@_register
class TestBeta(unittest.TestCase):
    def test_both(self):
        self.assertEqual(sorted(_REGISTRY), ["TestAlpha", "TestBeta"])
''',
    "r2": '''"""A metaclass that registers each class in a module list."""
import unittest

_SEEN = []


class _Meta(type):
    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        _SEEN.append(name)


class TestAlpha(unittest.TestCase, metaclass=_Meta):
    def test_a(self):
        self.assertIn("TestAlpha", _SEEN)


class TestBeta(unittest.TestCase, metaclass=_Meta):
    def test_b(self):
        self.assertEqual(len(_SEEN), 2)
''',
    "unchanged": '''"""A shared base whose class attributes no test changes."""
import unittest


def _fill(rows):
    rows["c"] = 3


class _Run:
    DATES = dict(a="2026-01-01")
    ROWS = [0]

    @classmethod
    def setUpClass(cls):
        cls.n = len(cls.DATES)
        cls.rows = list(cls.ROWS)
        cls.DATES_SEEN = {**cls.DATES}
        cls.ROWS = cls.ROWS + [1]       # binds TestAlpha's own, TestBeta's own


class TestAlpha(_Run, unittest.TestCase):
    def test_a(self):
        self.assertEqual(self.n, 1)


class TestBeta(_Run, unittest.TestCase):
    def test_b(self):
        self.assertEqual(self.rows, [0])
        self.assertEqual(self.ROWS, [0, 1])
''',
    "readonly": '''"""Module data read through methods that change nothing."""
import unittest

_ROWS = [1, 2, 2]
_NAMES = {"a": 1}


class TestAlpha(unittest.TestCase):
    def test_a(self):
        self.assertEqual(_ROWS.count(2), 2)


class TestBeta(unittest.TestCase):
    def test_b(self):
        self.assertEqual((_ROWS.index(2), _NAMES.get("a")), (1, 1))
''',
    "atimport": '''"""A module-level value changed by its own method at import."""
import unittest


class _Counter:
    def __init__(self):
        self.n = 0

    def bump(self):
        self.n += 1
        return self.n


_COUNTER = _Counter()
_FIRST = _COUNTER.bump()


class TestAlpha(unittest.TestCase):
    def test_a(self):
        self.assertEqual(_COUNTER.n, 1)


class TestBeta(unittest.TestCase):
    def test_b(self):
        self.assertEqual(_FIRST, 1)
''',
    "r6": '''"""A second name for a test class."""
import unittest


class TestAlpha(unittest.TestCase):
    def test_a(self):
        pass


class TestBeta(unittest.TestCase):
    def test_b(self):
        pass


TestAlphaAgain = TestAlpha
''',
    "made": '''"""A test class made by a call."""
import unittest


class TestAlpha(unittest.TestCase):
    def test_a(self):
        pass


class TestBeta(unittest.TestCase):
    def test_b(self):
        pass


def _make(n):
    def test_n(self):
        self.assertEqual(n, n)
    return type(f"TestMade{n}", (unittest.TestCase,), {"test_n": test_n})


_Row = type("_Row", (), {})     # a made class no test runs: no refusal
TestMade1 = _make(1)
''',
    "ownglobals": '''"""A test that reads its module's names by a string."""
import unittest

_LIMIT = 3


class TestAlpha(unittest.TestCase):
    def test_a(self):
        self.assertEqual(_LIMIT, 3)


class TestBeta(unittest.TestCase):
    def test_b(self):
        self.assertLessEqual(globals().get("_LIMIT", 0), 3)
''',
    "own": '''"""A test that imports its own module by name."""
import unittest


class TestAlpha(unittest.TestCase):
    value = 3

    def test_a(self):
        pass


class TestBeta(unittest.TestCase):
    def test_b(self):
        from test_fix_zz_own import TestAlpha as T
        self.assertEqual(T.value, 3)
''',
    "environ": '''"""Process state one class leaves for another."""
import os
import unittest


class TestAlpha(unittest.TestCase):
    def test_sets(self):
        os.environ["ZZ_SPLIT_FLAG"] = "1"


class TestBeta(unittest.TestCase):
    def test_reads(self):
        self.assertEqual(os.environ.get("ZZ_SPLIT_FLAG"), "1")
''',
    "importside": '''"""An import's own effect that a class does not read by name."""
import os
import unittest

from zz_side_helper import VALUE


class TestAlpha(unittest.TestCase):
    def test_value(self):
        self.assertEqual(VALUE, 3)


class TestBeta(unittest.TestCase):
    def test_flag(self):
        self.assertEqual(os.environ.get("ZZ_SIDE"), "1")
''',
    "decorated": '''"""A decorator whose store runs when a test runs, not when it is applied."""
import os
import unittest


def _tz(fn):
    def wrapped(*a):
        os.environ["ZZ_TZ"] = "UTC"
        return fn(*a)
    return wrapped


class TestAlpha(unittest.TestCase):
    @_tz
    def test_a(self):
        self.assertEqual(os.environ["ZZ_TZ"], "UTC")


class TestBeta(unittest.TestCase):
    @_tz
    def test_b(self):
        self.assertEqual(os.environ["ZZ_TZ"], "UTC")
''',
}
REVIEW["relative"] = REVIEW["own"].replace("from test_fix_zz_own import",
                                           "from .test_fix_zz_relative import")
SIDE_HELPER = '''"""Importing it sets a flag."""
import os

os.environ["ZZ_SIDE"] = "1"
VALUE = 3
'''
ALPHA, BETA = "features/misc/test_zz_alpha.py", "rules/ca/sl/test_zz_beta.py"


def _ab(name):
    return [(f"test_fix_zz_{name}::TestAlpha", ALPHA), (f"test_fix_zz_{name}::TestBeta", BETA)]


def _module(name):
    out = {f"tests/test_fix_zz_{name}.py": REVIEW[name]}
    if name == "importside":
        out["tests/zz_side_helper.py"] = SIDE_HELPER
    return out


class TestTheReviewsCases(_SplitCase):
    def test_state_two_files_would_share_is_refused(self):
        cases = {
            "argmut": "test_first (line 13) changes _LOG after import",
            "box": "test_bump (line 18) changes _COUNTER.bump() after import",
            "mixin": "_Seen (line 5) goes into 2 new files and has a class attribute that is a "
                     "list (seen, line 6), changed at line 9",
            "subclass": "_Registered (line 5) goes into 2 new files and has __init_subclass__",
            "r1": "line 13 (TestAlpha) changes module-level state when it is defined",
            "r2": "__init__ (line 8) changes _SEEN.append() after import",
            "r6": "line 15: TestAlphaAgain = TestAlpha, a second name for a test class",
            "made": "line 22: TestMade1 is made by a call (_make(1))",
            "own": "line 14 imports its own module by name (test_fix_zz_own)",
            "ownglobals": "line 12 (TestBeta) reads its own module's names by name (globals)",
            "relative": "line 14 imports its own module by name (test_fix_zz_relative)",
        }
        for name, why in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, {**SPLIT_EXTRA, **_module(name)})
                with self.assertRaises(SystemExit) as cm, _ruff(), redirect_stdout(io.StringIO()):
                    MT.move(r, _ab(name))
                self.assertIn(f"tests/test_fix_zz_{name}.py: {why}", str(cm.exception))

    def test_module_data_read_only_or_changed_at_import_is_split(self):
        r, *_rest = self.split(place=_ab("readonly"), extra=_module("readonly"))
        for f in (ALPHA, BETA):
            self.assertIn("_ROWS = [1, 2, 2]", (r / "tests" / f).read_text())
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tests").mkdir()
            (Path(td) / "tests" / "test_fix_zz_atimport.py").write_text(REVIEW["atimport"])
            plan = MT.SplitPlan(Path(td), "test_fix_zz_atimport", _places("atimport"))
        first = next(i for i, s in enumerate(plan.lay.stmts) if s.lineno == 15)
        self.assertEqual(plan.everywhere[first], "changes at import (_COUNTER.bump())")
        self.assertIn("\n_FIRST = _COUNTER.bump()\n", plan.texts[ALPHA])

    def test_a_decorator_that_changes_state_when_a_test_runs_is_split(self):
        r, *_rest = self.split(place=_ab("decorated"), extra=_module("decorated"))
        for f in (ALPHA, BETA):
            self.assertIn("def _tz(fn):", (r / "tests" / f).read_text())

    def test_an_import_that_runs_code_goes_into_every_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, **_module("importside")})
            plan = MT.SplitPlan(r, "test_fix_zz_importside", [(c, n) for c, n in (
                ("TestAlpha", ALPHA), ("TestBeta", BETA))])
        for new in (ALPHA, BETA):
            self.assertIn("\nfrom zz_side_helper import VALUE\n", plan.texts[new])
        self.assertEqual(plan.allow, {BETA: {"F401": 1}})     # unused there: F401 may count
        self.assertEqual((plan.ruff_bound(ALPHA), plan.ruff_bound(BETA)), ({}, {"F401": 1}))
        self.assertEqual(list(plan.effects.values()), ["zz_side_helper.py:4 stores into something"])
        self.assertIn("  in every file for what it runs at import: line 5", "\n".join(plan.describe()))
        # A helper that changes only its own values runs nothing that counts.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, **_module("importside"), "tests/zz_side_helper.py":
                           '"""H."""\nimport _hermetic\nENV = {"a": 1}\nENV.pop("a")\nVALUE = 3\n'})
            plan = MT.SplitPlan(r, "test_fix_zz_importside", _places("importside"))
        self.assertEqual(plan.effects, {})
        self.assertNotIn("zz_side_helper", plan.texts[BETA])
        # A package's __init__ runs too.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_zz_importside.py": REVIEW[
                "importside"].replace("from zz_side_helper", "from zz_pkg.zz_side_helper"),
                "tests/zz_pkg/__init__.py": '"""P."""\nimport os\nos.environ["ZZ_SIDE"] = "1"\n',
                "tests/zz_pkg/zz_side_helper.py": '"""H."""\nVALUE = 3\n'})
            self.assertEqual(MT.helper_files(r / "tests", "zz_pkg.zz_side_helper"),
                             [r / "tests/zz_pkg/__init__.py", r / "tests/zz_pkg/zz_side_helper.py"])
            self.assertEqual(MT.helper_files(r / "tests", "zz_pkg.nothing"),
                             [r / "tests/zz_pkg/__init__.py"])
            plan = MT.SplitPlan(r, "test_fix_zz_importside", _places("importside"))
        self.assertEqual(list(plan.effects.values()), ["zz_pkg/__init__.py:3 stores into something"])


class TestWhatAHelperRunsAtImport(unittest.TestCase):
    def test_each_kind_of_effect(self):
        inner = '"""I."""\nimport os\nos.environ["ZZ_SIDE"] = "1"\nVALUE = 3\n'
        cases = {
            "a store": (SIDE_HELPER, {}, "zz_side_helper.py:4 stores into something"),
            "a call": ('"""H."""\nimport os\nos.putenv("ZZ_SIDE", "1")\nVALUE = 3\n', {},
                       "zz_side_helper.py:3 runs code"),
            "a change": ('"""H."""\nimport os\n_OLD = os.environ.setdefault("ZZ_SIDE", "1")\n'
                         'VALUE = 3\n', {}, "zz_side_helper.py:3 changes os.setdefault()"),
            "through an import": ('"""H."""\nfrom zz_inner import VALUE\n',
                                  {"tests/zz_inner.py": inner},
                                  "zz_inner.py:3 stores into something"),
            "the runner's setup and its own values": (
                '"""H."""\nimport _hermetic\nENV = {"a": 1}\nENV.pop("a")\nVALUE = 3\n',
                {"tests/_hermetic/__init__.py": inner}, None),
        }
        for why, (helper, extra, want) in cases.items():
            with self.subTest(why), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, {**SPLIT_EXTRA, **_module("importside"),
                               "tests/zz_side_helper.py": helper, **extra})
                plan = MT.SplitPlan(r, "test_fix_zz_importside", _places("importside"))
                self.assertEqual(list(plan.effects.values()), [want] if want else [])


def _places(name):
    return [(o.split("::")[1], n) for o, n in _ab(name)]


def _record(**kw):
    """A part's record as run_outcomes gives it, KW's parts set."""
    out = {"lines": {}, "names": [], "asserts": [], "children": [], "product": {}, "values": {},
           "returns": {}, "cuts": {}, "tracer": False}
    out.update(kw)
    return out


class TestTheOutcomes(_SplitCase):
    def outcomes(self, name=None, patches=()):
        place = _ab(name) if name else SPLIT_PLACE
        with contextlib.ExitStack() as stack:
            for target in patches:
                stack.enter_context(mock.patch.object(MT.SplitPlan, target, lambda *a: None))
            r, _tree, _head, base, head = self.split(place=place,
                                                     extra=_module(name) if name else None)
            old = place[0][0].split("::")[0]
            plan = MT.SplitPlan(base, old, [(o.split("::")[1], n) for o, n in place])
        mods, classes = VT.batch_moves(base, head)
        with tempfile.TemporaryDirectory() as td:
            return VT.outcome_problems(base, head, {old: plan}, mods, classes, Path(td))

    def test_each_outcome_is_recorded(self):
        kinds = ('"""Outcomes of each kind."""\nimport unittest\n\n\n'
                 'class TestAlpha(unittest.TestCase):\n'
                 '    def test_fails(self):\n        self.fail("no")\n\n'
                 '    @unittest.skip("not today")\n    def test_skipped(self):\n        pass\n\n'
                 '    def test_subtests(self):\n        for n in (1, 2):\n'
                 '            with self.subTest(n=n):\n                self.assertEqual(n, 1)\n\n'
                 '    @unittest.expectedFailure\n    def test_xfail(self):\n        {}["k"]\n\n\n'
                 'class TestBeta(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n'
                 '        raise RuntimeError("no fixture")\n\n    def test_b(self):\n        pass\n')
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "tree"
            (tree / "tests").mkdir(parents=True)
            (tree / "tests" / "_hermetic.py").write_text('"""Stub."""\n')
            (tree / "tests" / "test_fix_zz_kinds.py").write_text(kinds)
            (tree / "tests" / "test_fix_zz_exit.py").write_text(
                '"""Exits 3 after its run."""\nimport atexit\nimport os\nimport unittest\n'
                'atexit.register(os._exit, 3)\n\n\nclass TestE(unittest.TestCase):\n'
                '    def test_e(self):\n        pass\n')
            (tree / "tests" / "test_fix_zz_gone.py").write_text(
                '"""Removes its working folder."""\nimport os\nimport tempfile\nimport unittest\n\n\n'
                'class TestGone(unittest.TestCase):\n    def test_gone(self):\n'
                '        d = tempfile.mkdtemp()\n        os.chdir(d)\n        os.rmdir(d)\n')
            # The part's seed is in its child's environment, never in the test's.
            (tree / "tests" / "test_fix_zz_seed.py").write_text(
                '"""The seed is the child\'s only."""\nimport os\nimport subprocess\nimport sys\n'
                'import unittest\n\n\nclass TestS(unittest.TestCase):\n    def test_s(self):\n'
                '        self.assertNotIn("_VERIFY_TEMP_SEED", os.environ)\n'
                '        r = subprocess.run([sys.executable, "-c", "import os; print(os.environ['
                '\'_VERIFY_TEMP_SEED\'])"], capture_output=True, text=True, check=True)\n'
                '        self.assertEqual(r.stdout, "<MOD>.TestS.test_s\\n")\n'
                '        self.assertNotIn("_VERIFY_TEMP_SEED", os.environ)\n')
            got = VT.run_outcomes(tree, ["test_fix_zz_kinds"], Path(td))
            ended = VT.run_outcomes(tree, ["test_fix_zz_exit"], Path(td))
            # Recorded, a test that removes its working folder: the run goes on.
            gone = VT.run_outcomes(tree, ["test_fix_zz_gone"], Path(td),
                                   {"own": "test_fix_zz_gone", "file": "test_fix_zz_gone.py"})
            rec = VT.run_outcomes(tree, ["test_fix_zz_kinds"], Path(td),
                                  {"own": "test_fix_zz_kinds", "file": "test_fix_zz_kinds.py"})
            seeded = VT.run_outcomes(tree, ["test_fix_zz_seed"], Path(td),
                                     {"own": "test_fix_zz_seed", "file": "test_fix_zz_seed.py"})
        k = "test_fix_zz_kinds."
        self.assertEqual({x: got[x] for x in ("tests", "ids", "held")},
                         {"tests": {k + "TestAlpha.test_fails": "fail: AssertionError",
                                    k + "TestAlpha.test_skipped": "skip: not today",
                                    k + "TestAlpha.test_subtests": "fail: AssertionError",
                                    k + "TestAlpha.test_xfail": "expected failure: KeyError",
                                    k + "TestBeta.test_b": "not run"},
                          "ids": [k + "TestAlpha.test_fails",
                                  k + "TestAlpha.test_skipped", k + "TestAlpha.test_subtests",
                                  k + "TestAlpha.test_xfail", k + "TestBeta.test_b"],
                          "held": [f"setUpClass ({k}TestBeta): error: RuntimeError"]})
        self.assertEqual(ended["tests"], {})
        self.assertTrue(ended["held"][0].startswith("the run of test_fix_zz_exit failed"), ended)
        self.assertEqual((gone["tests"], gone["held"]),
                         ({"test_fix_zz_gone.TestGone.test_gone": "pass"}, []))
        self.assertEqual(seeded["tests"], {"test_fix_zz_seed.TestS.test_s": "pass"})
        child = seeded["parts"]["test_fix_zz_seed.TestS.test_s"][0]["children"][0]
        # its exit, its environment per variable (but the seed, the verifier's), a line of output
        self.assertEqual((child[1], "HOME" in child[3], "_VERIFY_TEMP_SEED" in child[3],
                          len(child[4]), child[5]), (0, True, False, 1, []))
        # a line's finer record: the seed's name, its digits masked
        self.assertEqual(child[6][0][1:], [[], None, "'<MOD>.TestS.test_s'"])
        # Each test's parts and each fixture's: its lines counted (keyed by
        # the def, not the file's line), its assertions with their sites, a
        # subtest's parameters among them, a digest per argument.
        self.assertEqual(rec["tests"], got["tests"])
        self.assertEqual(sorted(rec["parts"]), sorted(
            [k + t for t in ("TestAlpha.test_fails", "TestAlpha.test_skipped",
                             "TestAlpha.test_subtests", "TestAlpha.test_xfail")]
            + ["setUpClass TestAlpha", "setUpClass TestBeta", "setUpModule",
               "tearDownClass TestAlpha", "tearDownClass TestBeta", "tearDownModule"]))
        sub = rec["parts"][k + "TestAlpha.test_subtests"][0]
        self.assertEqual(sub["lines"], {"<own>|TestAlpha.test_subtests|0|0|1": 3,
                                        "<own>|TestAlpha.test_subtests|0|0|2": 4,
                                        "<own>|TestAlpha.test_subtests|0|0|3": 2})
        self.assertEqual([a[:2] for a in sub["asserts"]], [
            ["subTest", "<own>|TestAlpha.test_subtests|0|0|2"],
            ["assertEqual", "<own>|TestAlpha.test_subtests|0|0|3"],
            ["subTest", "<own>|TestAlpha.test_subtests|0|0|2"],
            ["assertEqual", "<own>|TestAlpha.test_subtests|0|0|3"]])
        self.assertEqual([[x[0] for x in a[2]] for a in sub["asserts"]],
                         [["n"], ["1", "2"], ["n"], ["1", "2"]])
        self.assertEqual(len({json.dumps(a[2]) for a in sub["asserts"]}), 4)
        # each argument's finer record: its digest with the digit runs
        # masked, the runs, its pieces (none for a number), a short repr
        self.assertEqual(sub["asserts"][1][2][0][2][1:], [["1"], None, "1"])
        self.assertIn("subTest", sub["names"])               # what its code names
        self.assertIn("<own>", sub["loaded"])                # the modules loaded at its start
        self.assertEqual(rec["parts"]["setUpClass TestBeta"][0]["lines"],
                         {"<own>|TestBeta.setUpClass|0|0|2": 1})

    def test_only_an_unconditional_skip_is_one_by_hand(self):
        # A name bound to unittest.skip by a condition (z_condskip) or an
        # assignment is no decorator applied directly: its skip is not by hand.
        skips = ('"""Skips."""\nimport functools\nimport shutil\nimport unittest\n'
                 'import unittest as ut\nfrom unittest import skip\n\n'
                 'later = functools.partial(unittest.skipUnless, False)\n'
                 'REASON = "a name, not a constant"\n'
                 'maybe = unittest.skip if not shutil.which("zz-no-such-tool") else (lambda why: '
                 'lambda f: f)\nalias = unittest.skip\n\n\nclass TestS(unittest.TestCase):\n'
                 '    @unittest.skip("by hand")\n    def test_a(self):\n        pass\n\n'
                 '    @ut.skip("by hand, another name")\n    def test_b(self):\n        pass\n\n'
                 '    @skip("by hand, imported")\n    def test_c(self):\n        pass\n\n'
                 '    @later("an alias of skipUnless")\n    def test_d(self):\n        pass\n\n'
                 '    @unittest.skip(REASON)\n    def test_e(self):\n        pass\n\n'
                 '    @unittest.skipIf(True, "a condition")\n    def test_f(self):\n        pass\n\n'
                 '    def test_g(self):\n        self.skipTest("at run time")\n\n'
                 '    @maybe("needs zz-no-such-tool")\n    def test_h(self):\n        pass\n\n'
                 '    @alias("an alias")\n    def test_i(self):\n        pass\n\n\n'
                 '@unittest.skip("a class by hand")\nclass TestT(unittest.TestCase):\n'
                 '    def test_t(self):\n        pass\n')
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "tree"
            (tree / "tests").mkdir(parents=True)
            (tree / "tests" / "_hermetic.py").write_text('"""Stub."""\n')
            (tree / "tests" / "test_fix_zz_skips.py").write_text(skips)
            got = VT.run_outcomes(tree, ["test_fix_zz_skips"], Path(td),
                                  {"own": "test_fix_zz_skips", "file": "test_fix_zz_skips.py",
                                   "by_hand": True})
        self.assertEqual(sorted(got["by_hand"]), ["TestS.test_a", "TestS.test_b", "TestS.test_c",
                                                  "TestT"])
        self.assertEqual(VT.base_problems("test_fix_zz_skips", got), [
            "outcomes: tests/test_fix_zz_skips.py: 6 test(s) do not pass at BASE, so nothing shows "
            "what they do or check: TestS.test_d (skip: an alias of skipUnless), TestS.test_e "
            "(skip: a name, not a constant), TestS.test_f (skip: a condition), TestS.test_g (skip: "
            "at run time), TestS.test_h (skip: needs zz-no-such-tool), TestS.test_i (skip: an "
            "alias) (compared: a pass, an expected failure, an unconditional `@unittest.skip` "
            "with a constant reason): fix or prep the module so they pass here, or move it whole"])

    def test_each_part_is_compared(self):
        own = "<own>|TestB.test_b|0|0|"
        ret = "<own>|TestB.test_b|0|0|r|"
        base = _record(lines={own + "1": 2, own + "2": 1}, names=["_LOG", "environ"],
                       values={"<own>|_LOG": "d1", "<env>|ZZ_A": "e1"},
                       asserts=[["assertGreater", own + "2", [["1", "a1"], ["2", "a2"],
                                                             ["msg", "m1"]]]],
                       children=[[["<PY>", "-c", "x"], 0, "c1", {"ZZ_A": "v1"}, ["o1", "o2"], []]],
                       returns={ret + "n": {"n1": 1}, ret + "<value>": {"r1": 1}},
                       product={"src/taxjson/x.py": [3, 4]})

        def asserting(*args):
            return [["assertGreater", own + "2", [list(x) for x in zip(("1", "2", "msg"), args)]]]

        def child(cwd="c1", env="v1", out=("o1", "o2"), rc=0):
            return [[["<PY>", "-c", "x"], rc, cwd, {"ZZ_A": env}, list(out), []]]
        said = "child process #1 (`<PY> -c x`, exit 0) "
        cases = {
            "the same": ({}, []),
            "a line": ({"lines": {own + "1": 1}},
                       ["TestB.test_b line +1 ran 2 time(s) at BASE, 1 after",
                        "TestB.test_b line +2 ran 1 time(s) at BASE, 0 after"]),
            "an assertion's argument": ({"asserts": asserting("a9", "a2", "m1")},
                                        ["assertion #1 (assertGreater at TestB.test_b line +2) "
                                         "has another argument 1"]),
            "an assertion's message": ({"asserts": asserting("a1", "a2", "m9")},
                                       ["assertion #1 (assertGreater at TestB.test_b line +2) "
                                        "has another msg="]),
            "an assertion fewer": ({"asserts": []},
                                   ["assertion #1 at BASE, assertGreater at TestB.test_b line +2, "
                                    "is not made after"]),
            "a child's line": ({"children": child(out=("o1", "o9"))}, [said + "prints another line 2"]),
            "a child's line more": ({"children": child(out=("o1", "o2", "o3"))},
                                    [said + "prints another line 3",
                                     said + "prints another number of lines"]),
            "a child's variable": ({"children": child(env="v2")},
                                   [said + "runs with another ZZ_A in its environment"]),
            "a child's folder": ({"children": child(cwd="c2")}, [said + "runs in another folder"]),
            "a child's exit": ({"children": child(rc=1)},
                               ["child process #1 at BASE, `<PY> -c x`, exit 0, is `<PY> -c x`, "
                                "exit 1 after"]),
            "a value read": ({"values": {"<own>|_LOG": "d2", "<env>|ZZ_A": "e1"}},
                             ["_LOG holds another value at its start"]),
            # a name the new file does not hold: a NameError would say it
            "a value not held": ({"values": {"<env>|ZZ_A": "e1"}}, []),
            "a variable unset": ({"values": {"<own>|_LOG": "d1"}},
                                 ["the environment's ZZ_A is unset at its start"]),
            # unset where no code that ran names the environment: not read
            "a variable no code reads": ({"values": {"<own>|_LOG": "d1"}, "names": ["_LOG"]}, []),
            "a local": ({"returns": {ret + "n": {"n2": 1}, ret + "<value>": {"r1": 1}}},
                        ["TestB.test_b's local n differs"]),
            "a return value": ({"returns": {ret + "n": {"n1": 1}, ret + "<value>": {"r2": 1}}},
                               ["TestB.test_b's return value differs"]),
            "a product line more": ({"product": {"src/taxjson/x.py": [3, 4, 5]}}, []),
            "a product line fewer": ({"product": {"src/taxjson/x.py": [3]}},
                                     ["1 product line(s) BASE reached are not reached after: "
                                      "src/taxjson/x.py:4"]),
        }
        for why, (head, want) in cases.items():
            with self.subTest(why):
                self.assertEqual(VT.part_problems([base] * 3, [{**base, **head}] * 2),
                                 (want, [], False))
        # What the BASE runs do not agree on is dropped, each part by itself,
        # and listed (key, words); what they agree on is still compared; a
        # product line one BASE run did not reach is not required.
        other = {**base, "lines": {own + "1": 3, own + "2": 1},
                 "values": {"<own>|_LOG": "d3", "<env>|ZZ_A": "e1"},
                 "asserts": asserting("a1", "a3", "m1"), "children": child(out=("o3", "o2")),
                 "returns": {ret + "n": {"n3": 1}, ret + "<value>": {"r1": 1}},
                 "product": {"src/taxjson/x.py": [3]}}
        head = {**base, "lines": {own + "1": 7, own + "2": 1},
                "values": {"<own>|_LOG": "d9", "<env>|ZZ_A": "e1"},
                "asserts": asserting("a9", "a9", "m1"), "children": child(out=("o9", "o2")),
                "returns": {ret + "n": {"n9": 1}, ret + "<value>": {"r1": 1}}, "product": {}}
        self.assertEqual(VT.part_problems([base, other, base], [head, head]), (
            ["assertion #1 (assertGreater at TestB.test_b line +2) has another argument 1",
             "1 product line(s) BASE reached are not reached after: src/taxjson/x.py:3"],
            [(f"line {own}1", "the count of TestB.test_b line +1, at BASE 2/3/2"),
             ("assertion #1 arg 2", "assertion #1's arg 2"),
             ("child process #1 stdout line 1", "child process #1's stdout line 1"),
             ("value <own>|_LOG", "the value of _LOG"),
             (f"local {ret}n", "TestB.test_b's local n")], False))
        # A digest cut (DEPTH levels, LIMIT elements) is a part dropped, its kind named.
        cut = {**base, "cuts": {f"local {ret}n": "depth", "assertion #1 arg 1": "size",
                                "value <env>|ZZ_A": "depth/size"}}
        self.assertEqual(VT.part_problems([base, cut, base], [base, base])[1], [
            ("cut assertion #1 arg 1", "assertion #1 arg 1 cut at 100,000 elements"),
            (f"cut local {ret}n", "TestB.test_b's local n cut at 60 levels"),
            ("cut value <env>|ZZ_A",
             "the value of the environment's ZZ_A cut at 60 levels and 100,000 elements")])
        # A difference any HEAD run shows stands (a value drawn from
        # randomness an earlier class seeded may match in one).
        self.assertEqual(VT.part_problems([base] * 3, [
            {**base, "values": {"<own>|_LOG": "d2", "<env>|ZZ_A": "e1"}}, base]),
            (["_LOG holds another value at its start"], [], False))
        # A HEAD value some BASE run lacks, most of them holding it or
        # fewer than half (the odd run out): each such part dropped, the
        # runs named (a row accepts it, saying why the value is flaky).
        ex: dict = {}
        d, dropped, _l = VT.part_problems([base, other, base], [base, base], ex)
        self.assertEqual(d, [])
        self.assertEqual(dropped[0], (f"line {own}1", "the count of TestB.test_b line +1, after "
                                      "what 2, not all, of the 3 BASE runs hold (BASE run 1+3 / "
                                      "1+3; at BASE 2/3/2, after 2/2)"))
        self.assertEqual(len(dropped), 5, dropped)
        self.assertNotIn("matched", ex)
        d, dropped, _l = VT.part_problems([base, other, base], [other, base])
        self.assertEqual(d, [])
        self.assertEqual(dropped[0], (f"line {own}1", "the count of TestB.test_b line +1, after "
                                      "what only 1 of the 3 BASE runs hold (BASE run 2 / 1+3; "
                                      "at BASE 2/3/2, after 3/2)"))
        self.assertEqual([k for k, _t in dropped],
                         [f"line {own}1", "assertion #1 arg 2", "child process #1 stdout line 1",
                          "value <own>|_LOG", f"local {ret}n"])
        # A loop's count off by a few, and nothing else: labelled (the
        # assertions at its lines aside), refused all the same.
        loop = {**base, "lines": {own + "1": 388, own + "2": 1}}
        self.assertEqual(VT.part_problems([loop] * 3, [{**loop, "lines": {own + "1": 389,
                                                                           own + "2": 1}}, loop]),
                         (["TestB.test_b line +1 ran 388 time(s) at BASE, 388/389 after"], [], True))
        polled = {**base, "lines": {own + "1": 3, own + "2": 1},
                  "asserts": [["assertLess", own + "1", [["1", "a"]]]] * 3}
        more = {**polled, "lines": {own + "1": 4, own + "2": 1},
                "asserts": [["assertLess", own + "1", [["1", "a"]]]] * 4}
        d, dropped, loopish = VT.part_problems([polled] * 3, [more] * 2)
        self.assertEqual((len(d), dropped, loopish), (2, [], True), d)
        for head in ({**loop, "lines": {own + "1": 389, own + "2": 0}},     # another line too
                     {**loop, "lines": {own + "1": 300, own + "2": 1}}):    # not by a few
            with self.subTest(head=head["lines"]):
                self.assertFalse(VT.part_problems([loop] * 3, [head] * 2)[2])
        self.assertEqual(VT._where("tests/_support/x.py|note|3|1"), "tests/_support/x.py:4 (note)")
        self.assertEqual(VT._where("<own>|TestA.test_a.<locals>.<lambda>|2|1|0"),
                         "TestA.test_a.<locals>.<lambda> (nested at +2) line +0")

    def test_the_finer_comparison(self):
        # What the BASE runs disagree on is compared finer before it is
        # dropped: with the digit runs masked (the runs they agree on still
        # compared), then piece by piece; only what is still unstable drops,
        # the BASE runs' values shown.
        ret = "<own>|TestB.test_b|0|0|r|"

        def doc(at, hour, masked="m"):
            # a local's one call: [digest, masked, runs, pieces, show]
            return [f"d{at}{hour}", masked, [str(at), str(hour)], None,
                    f"{{'at': {at}, 'hour': {hour}}}"]

        def run(*calls):
            return _record(returns={ret + "doc": {c[0]: 1 for c in calls}},
                           rfine={ret + "doc": list(calls)})
        bases = [run(doc(1731, 9)), run(doc(1732, 9)), run(doc(1733, 9))]
        extra = {}
        self.assertEqual(VT.part_problems(bases, [run(doc(1734, 9))] * 2, extra), ([], [], False))
        self.assertEqual(extra["waived"], [f"`local {ret}doc` (1731/1732/1733 -> 1734/1734)"])
        self.assertEqual(VT.part_problems(bases, [run(doc(1734, 0))] * 2), (
            ["TestB.test_b's local doc differs beyond the digits the BASE runs disagree on"], [],
            False))
        # Piece by piece where the masked digest differs too (a key's text):
        # the stable piece compared, the unstable one dropped with its values.

        def pieced(at, hour, stamp):
            pieces = [["['at']", f"a{stamp}", f"m{stamp}", None, repr(stamp)],
                      ["['hour']", f"h{hour}", "mh", [str(hour)], str(hour)]]
            return [f"d{at}{hour}{stamp}", f"m{stamp}", None, pieces, "{...}"]
        bases = [run(pieced(1, 9, "x1")), run(pieced(2, 9, "y2")), run(pieced(3, 9, "z3"))]
        self.assertEqual(VT.part_problems(bases, [run(pieced(4, 9, "w4"))] * 2), (
            [], [(f"local {ret}doc ['at']", "TestB.test_b's local doc ['at'], at BASE 'x1' / 'y2' / "
                                           "'z3'")], False))
        self.assertEqual(VT.part_problems(bases, [run(pieced(4, 0, "w4"))] * 2)[0],
                         ["TestB.test_b's local doc ['hour'] differs"])
        # The assertions the BASE runs disagree on (a poll's): compared site by
        # site, only the unstable site dropped; a key a row may not accept.
        a1, a2 = "<own>|TestB.test_b|0|0|1", "<own>|TestB.test_b|0|0|3"

        def asserted(n, arg="x"):
            return _record(asserts=[["assertEqual", a1, [["1", arg, None]]]]
                           + [["assertTrue", a2, [["1", "t", None]]]] * n)
        self.assertEqual(VT.part_problems([asserted(3), asserted(4), asserted(3)],
                                          [asserted(5, "y")] * 2), (
            ["assertion 1 of 1 at TestB.test_b line +1 (assertEqual) has another argument 1"],
            [(f"assertions at {a2}", "the assertions at TestB.test_b line +3, which and how many: "
                                     "at BASE 3 (assertTrue) / 4 (assertTrue) / 3 (assertTrue)")],
            False))
        # A module-level name the BASE runs read that HEAD lacks, its module
        # loaded at the part's start: ABSENT (z_absent); not loaded there: not
        # compared, counted; a product name not looked for there: the same.
        seen = "_support.zz_seen|SEEN"
        base = _record(names=["SEEN", "cache_info", "is_iso"], loaded=["<own>", "_support.zz_seen"],
                       values={seen: "s1", "<prod>|zzcache|is_iso.cache_info": "c1"})
        for why, head, want, unloaded in (
                ("loaded", {"values": {}}, ["_support.zz_seen.SEEN is unset at its start"],
                 {"zzcache": 1}),
                ("product loaded", {"values": {seen: "s1"}, "loaded": ["<own>", "_support.zz_seen",
                                                                       "zzcache"]},
                 ["zzcache.is_iso.cache_info is unset at its start"], {}),
                ("not loaded", {"values": {}, "loaded": ["<own>"]}, [],
                 {"_support.zz_seen": 1, "zzcache": 1}),
                ("not looked for", {"values": {seen: "s1"}, "loaded": [
                    "<own>", "_support.zz_seen", "zzcache"], "unseen": ["is_iso"]}, [],
                 {"zzcache": 1})):
            with self.subTest(why):
                extra = {}
                self.assertEqual(VT.part_problems([base] * 3, [{**base, **head}] * 2, extra),
                                 (want, [], False))
                self.assertEqual(extra["unloaded"], unloaded)
        # A name of OLD's a new file lacks that holds what OLD's import left
        # (a constant: `"HEAD"` a string beside it, or a helper moved to
        # _support that reads it now): not compared (TestTheSeventhRounds-
        # Comparison: the rest).
        own = _record(names=["HEAD"], loaded=["<own>"], values={"<own>|HEAD": "h1"},
                      imported={"HEAD": "h1"})
        for held in ({"_support.x|HEAD": "h1"}, {}):
            with self.subTest(held=held):
                self.assertEqual(VT.part_problems(
                    [own] * 3, [{**own, "loaded": ["<own>", "_support.x"], "values": held}] * 2),
                    ([], [], False))
        # A line count the BASE runs disagree on: dropped with its counts; an
        # accepted one stays nonzero and in BASE's range, widened (bounds).
        line = "<own>|TestB.test_b|0|0|3"
        polled = [_record(lines={line: n}) for n in (40, 50, 45)]
        for after, bound in ((47, None), (55, None), (0, "TestB.test_b line +3 ran 0 time(s) "
                                                         "after, 40 to 50 at BASE (an accepted "
                                                         "count stays nonzero where BASE's is, "
                                                         "and within BASE's range widened by 5)"),
                             (56, "TestB.test_b line +3 ran 56 time(s) after, 40 to 50 at BASE "
                                  "(an accepted count stays nonzero where BASE's is, and within "
                                  "BASE's range widened by 5)")):
            with self.subTest(after=after):
                extra = {}
                self.assertEqual(VT.part_problems(polled, [_record(lines={line: after})] * 2,
                                                  extra)[1],
                                 [(f"line {line}", "the count of TestB.test_b line +3, at BASE "
                                                   "40/50/45")])
                self.assertEqual(extra["bounds"].get(f"line {line}"), bound)

    def test_which_values_a_part_reads(self):
        # value_read: what the code that ran names (its co_names, its
        # identifier string constants) keeps a value its start recorded.
        trig = VT._TRIG
        for key, names, want in (
                ("<own>|SEEN", {"SEEN"}, True), ("<own>|SEEN", {"seen"}, False),
                ("tests/_support/zz.py|SEEN", {"getattr", "SEEN"}, True),
                ("<env>|ZZ_A", {"environ"}, True), ("<env>|ZZ_A", {"getenv"}, True),
                ("<env>|ZZ_A", {"ZZ_A"}, False),
                ("<cwd>", {"getcwd"}, True), ("<cwd>", {"cwd"}, True), ("<cwd>", {"path"}, False),
                ("<decimal>", {"Decimal"}, True), ("<decimal>", {"localcontext"}, True),
                ("<decimal>|flags", {"getcontext"}, False),
                ("<decimal>|flags", {"getcontext", "flags"}, True),
                ("<class>|zz_rows", {"zz_rows"}, True), ("<class>|zz_rows", {"rows"}, False),
                ("<prod>|zzcache|is_iso.cache_info", {"is_iso", "cache_info"}, True),
                ("<prod>|zzcache|is_iso.cache_info", {"is_iso"}, False)):
            with self.subTest(key=key, names=names):
                self.assertIs(VT.value_read(key, frozenset(names), trig), want)
        # The recorder runs the verifier's rule itself.
        self.assertTrue(VT._RECORD.startswith("def value_read("))

    def test_the_unstable_table(self):
        # A reason, written by hand, names the unstable source and the line
        # that makes it (`:NNN`); a site's assertions or child processes are
        # never accepted whole (prep the module instead).
        rows, bad = VT.read_unstable(
            "test id\tpart key\twhy\n# a comment\n\n"
            "a.TestA.test_a\tline <own>|TestA.test_a|0|0|1\ta poll's deadline, test_x.py:12\n"
            "a.TestA.test_a\tvalue <own>|_X\n"
            "a.TestA.test_a\tline <own>|TestA.test_a|0|0|1\tagain\n"
            "a.TestA.test_b\t\tno key\n"
            "a.TestA.test_c\tlocal <own>|TestA.test_c|0|0|r|doc\tholds a timestamp\n"
            "a.TestA.test_c\tlocal <own>|TestA.test_c|0|0|r|n\tunstable at :14\n"
            "a.TestA.test_c\tassertions at <own>|TestA.test_c|0|0|3\tthe thread order, :15\n"
            "a.TestA.test_c\tchild processes <PY> -c x\tthe clock, :16\n"
            "a.TestA.test_c\tlocal <own>|TestA.test_c|0|0|r|t\tTime.time() at :17\n")
        self.assertEqual(rows, {("a.TestA.test_a", "line <own>|TestA.test_a|0|0|1"):
                                "a poll's deadline, test_x.py:12",
                                ("a.TestA.test_c", "local <own>|TestA.test_c|0|0|r|t"):
                                "Time.time() at :17"})
        reason = ("the reason names no unstable source (time, clock, mtime, stat, pid, thread, "
                  "poll, random, temp, port, hash, order, environment) and its line (`:NNN`)")
        self.assertEqual(bad, [
            "scripts/step9-unstable.tsv:5: not `test id<TAB>part key<TAB>why`",
            "scripts/step9-unstable.tsv:6: a.TestA.test_a line <own>|TestA.test_a|0|0|1 accepted "
            "twice",
            "scripts/step9-unstable.tsv:7: not `test id<TAB>part key<TAB>why`",
            f"scripts/step9-unstable.tsv:8: {reason}", f"scripts/step9-unstable.tsv:9: {reason}",
            "scripts/step9-unstable.tsv:10: accepts a site's assertions whole: prep the module "
            "instead",
            "scripts/step9-unstable.tsv:11: accepts a site's child processes whole: prep the "
            "module instead"])

    def test_the_text_check_lists_the_unstable_table(self):
        def table(text):
            def edit(r):
                (r / "scripts").mkdir(exist_ok=True)
                (r / VT.UNSTABLE).write_text(text)
            return edit
        r, tree, head_tree, base, head = self.split(
            edit=table("test id\tpart key\twhy\nx.TestA.test_a\tline k\ta poll at :3\n"))
        problems, listed = self.verify_text(r, tree, head_tree, base, head)
        self.assertEqual(problems, [])
        self.assertIn(VT.UNSTABLE, listed)
        r, tree, head_tree, base, head = self.split(edit=table("x.TestA.test_a\tline k\n"))
        problems, _listed = self.verify_text(r, tree, head_tree, base, head)
        self.assertEqual(problems, [
            "text: scripts/step9-unstable.tsv:1: not `test id<TAB>part key<TAB>why`"])

    def test_the_comparison_names_each_difference(self):
        plan = mock.Mock(placed={"TestA": "x/test_y.py", "TestB": "x/test_z.py"},
                         news=["x/test_y.py", "x/test_z.py"], lay=mock.Mock(stmts=[]))
        classes = {"test_fix_q.TestA": "x.test_y", "test_fix_q.TestB": "x.test_z"}
        a, b = "test_fix_q.TestA.test_a", "test_fix_q.TestB.test_b"
        whole = {"tests": {a: "pass", b: "skip: why"}, "parts": {}, "held": [],
                 "by_hand": ["TestB.test_b"]}
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        bt, ht = Path(td.name) / "b", Path(td.name) / "h"
        (ht / "scripts").mkdir(parents=True)

        def y(outcome="pass", parts=None, held=()):
            return {"x.test_y": {"tests": {"x.test_y.TestA.test_a": outcome}, "held": list(held),
                                 "parts": parts or {}}}

        def z(outcome="skip: why", held=(), **kw):
            return {"x.test_z": {"tests": {"x.test_z.TestB.test_b": outcome}, "held": list(held),
                                 "parts": {}, **kw}}

        def runs(head, bases=None):
            out = {**y(), **z(), **head}
            left = iter(bases or [whole] * 3)
            return lambda tree, names, td, record=None: (
                next(left) if tree == bt else out[names[0]])

        def compare(head, bases=None):
            with mock.patch.object(VT, "run_outcomes", runs(head, bases)):
                return VT.outcome_problems(bt, ht, {"test_fix_q": plan}, {}, classes, Path("t"))
        own = "<own>|TestA.test_a|0|0|"

        def looped(n):
            return {a: [_record(lines={own + "1": n})]}
        unstable = "outcomes: tests/test_fix_q.py: x.test_y.TestA.test_a: 1 part(s) differ between " \
            f"the BASE runs, so nothing shows them the same after the split: the count of " \
            f"TestA.test_a line +1, at BASE 388/390/388 (`line {own}1`): settle them by a prep " \
            f"edit, or accept each in scripts/step9-unstable.tsv (the test id, the part key, why)"
        q = "outcomes: tests/test_fix_q.py: "
        cases = {
            "the same": ({}, None, []),
            "a skip's reason": (z("skip: other"), None,
                                [q + "1 test(s) differ after the split: x.test_z.TestB.test_b "
                                 "skip: why -> skip: other"]),
            "a fixture's error after": (z(held=["setUpClass (x.test_z.TestB): error"]), None,
                                        [q + "the fixtures' errors differ: "
                                         "['setUpClass (TestB): error']"]),
            # an expected failure's exception is compared too
            "an exception of another type": (
                {**y("expected failure: TypeError"), **z("pass")},
                [{**whole, "tests": {a: "expected failure: KeyError", b: "pass"}}] * 3,
                [q + "1 test(s) differ after the split: x.test_y.TestA.test_a expected failure: "
                 "KeyError -> expected failure: TypeError"]),
            # a test twice in BASE's run (a second name for its class), once in HEAD's
            "an id lost": ({"x.test_y": {**y()["x.test_y"], "ids": ["x.test_y.TestA.test_a"]},
                            **z(ids=["x.test_z.TestB.test_b"])},
                           [{**whole, "ids": [a, a, b]}] * 3,
                           [q + "the ids: 1 of BASE's (renamed) missing, 0 new: "
                            "['x.test_y.TestA.test_a']"]),
            # a class's tests that no line places (made by a call)
            "a test of a class no line places": (
                {}, [{**whole, "tests": {**whole["tests"],
                                         "test_fix_q._make.<locals>.T.test_n": "pass"}}] * 3,
                [q + "1 test(s) differ after the split: test_fix_q._make.<locals>.T.test_n pass "
                 "-> absent",
                 q + "tests of _make.<locals>.T, a class no line places (made by a call, or bound "
                 "so): write it as a class of its own by hand first"]),
            # a test that traces itself: the recording cannot see it
            "a tracer": ({}, [{**whole, "parts": {a: [_record(tracer=True)]}}] * 3,
                         [q + "test_fix_q.TestA.test_a trace(s) itself (sys.settrace, "
                          "sys.setprofile or sys.monitoring), which the proof's recording uses: "
                          "move the module whole"]),
            # BASE's run shows what every test does: a skip but by hand, a
            # failure, a fixture's error or skip refuse
            "a skip not by hand": ({}, [{**whole, "by_hand": []}] * 3,
                                   [q + "1 test(s) do not pass at BASE, so nothing shows what "
                                    "they do or check: TestB.test_b (skip: why) (compared: a "
                                    "pass, an expected failure, an unconditional `@unittest.skip` "
                                    "with a constant reason): fix or prep the module so they pass "
                                    "here, or move it whole"]),
            "a failure at BASE": (y("fail: AssertionError"),
                                  [{**whole, "tests": {a: "fail: AssertionError", b: "skip: why"}}]
                                  * 3,
                                  [q + "1 test(s) do not pass at BASE, so nothing shows what they "
                                   "do or check: TestA.test_a (fail: AssertionError) (compared: a "
                                   "pass, an expected failure, an unconditional `@unittest.skip` "
                                   "with a constant reason): fix or prep the module so they pass "
                                   "here, or move it whole"]),
            "a fixture's skip at BASE": (
                z(held=["setUpClass (x.test_z.TestB): skip: no tool"]),
                [{**whole, "held": ["setUpClass (test_fix_q.TestB): skip: no tool"]}] * 3,
                [q + "a fixture fails or skips at BASE: setUpClass (test_fix_q.TestB): skip: no "
                 "tool: fix or prep the module so it runs here, or move it whole"]),
            # only a loop's count differs, by a few: labelled, refused
            "a loop's count": (y(parts={"x.test_y.TestA.test_a": [_record(lines={own + "1": 389})]}),
                               [{**whole, "parts": looped(388)}] * 3,
                               [q + "x.test_y.TestA.test_a does or checks otherwise after the "
                                "split: TestA.test_a line +1 ran 388 time(s) at BASE, 389 after "
                                "(only a loop's count differs, by a few: rerun it; the same counts "
                                "again are real)"]),
            # a part the BASE runs disagree on: refused until accepted (a
            # HEAD run that is one BASE run's is no difference)
            "a part unstable at BASE": (
                y(parts={"x.test_y.TestA.test_a": [_record(lines={own + "1": 389})]}),
                [{**whole, "parts": looped(n)} for n in (388, 390, 388)], [unstable]),
        }
        for why, (head, bases, want) in cases.items():
            with self.subTest(why):
                n = 3 if "no line places" in why else 2
                self.assertEqual(compare(head, bases), (want, n, []))
        # Outcomes the BASE runs do not agree on: nothing shows what it checks.
        flaky = {**whole, "tests": {a: "fail: AssertionError", b: "skip: why"}}
        problems, _n, _notes = compare({}, [whole, flaky, whole])
        self.assertTrue(any(p.startswith(
            q + "1 test(s) end otherwise in the BASE runs, so nothing shows what they check: "
            "test_fix_q.TestA.test_a ") and p.endswith(
            ": settle it by a prep edit, or move the module whole") for p in problems), problems)
        # Accepted in HEAD's scripts/step9-unstable.tsv: each acceptance a
        # note, a row that accepts nothing this time one too; a bad row refuses.
        (ht / VT.UNSTABLE).write_text(
            "test id\tpart key\twhy\n"
            f"x.test_y.TestA.test_a\tline {own}1\ta poll's count, test_fix_q.py:12\n"
            "x.test_y.TestA.test_a\tvalue <own>|_X\ta temp folder's name, test_fix_q.py:3\n"
            "x.test_y.TestB.test_q\tline k\tthe thread order, test_fix_q.py:30\n")
        unstable_run = (y(parts={"x.test_y.TestA.test_a": [_record(lines={own + "1": 389})]}),
                        [{**whole, "parts": looped(n)} for n in (388, 390, 388)])
        accepted = (f"accepted as unstable (scripts/step9-unstable.tsv): x.test_y.TestA.test_a: "
                    f"`line {own}1`: a poll's count, test_fix_q.py:12")
        self.assertEqual(compare(*unstable_run), ([], 2, [
            accepted,
            "scripts/step9-unstable.tsv accepts x.test_y.TestA.test_a `value <own>|_X`, which no "
            "BASE run dropped this time: the row goes"]))
        # Accepted, a line's count still keeps its bounds: nonzero where every
        # BASE run's is (z_poll0), within their range widened by 2 or a tenth.
        self.assertEqual(compare(y(parts={"x.test_y.TestA.test_a": [_record(lines={})]}),
                                 unstable_run[1])[0], [
            q + f"x.test_y.TestA.test_a: `line {own}1`, accepted as unstable, breaks its bounds: "
            "TestA.test_a line +1 ran 0 time(s) after, 388 to 390 at BASE (an accepted count "
            "stays nonzero where BASE's is, and within BASE's range widened by 39)"])
        (ht / VT.UNSTABLE).write_text(f"x.test_y.TestA.test_a\tline {own}1\n")
        self.assertEqual(compare(*unstable_run)[0], [
            "outcomes: scripts/step9-unstable.tsv:1: not `test id<TAB>part key<TAB>why`",
            unstable])

    def test_a_split_keeps_every_outcome(self):
        self.assertEqual(self.outcomes(), ([], 4, []))

    def test_order_dependence_is_refused(self):
        problems, n, _notes = self.outcomes("environ")
        self.assertEqual(n, 2)
        self.assertIn("outcomes: tests/test_fix_zz_environ.py: 1 test(s) differ after the split: "
                      "rules.ca.sl.test_zz_beta.TestBeta.test_reads pass -> fail: AssertionError",
                      problems)
        self.assertTrue(any(p.startswith(
            "outcomes: tests/test_fix_zz_environ.py: rules.ca.sl.test_zz_beta.TestBeta.test_reads "
            "does or checks otherwise after the split: assertion #1 (assertEqual at "
            "TestBeta.test_reads line +1) has another argument 1; the environment's "
            "ZZ_SPLIT_FLAG is unset at its start") for p in problems), problems)

    def test_a_test_importing_its_own_module_fails_after_the_split(self):
        problems, _n, _notes = self.outcomes("own", ("_own_imports",))
        self.assertTrue(any("test_fix_zz_own.TestBeta.test_b pass -> error" in p.replace(
            "rules.ca.sl.test_zz_beta", "test_fix_zz_own") for p in problems), problems)

    def test_an_import_kept_for_what_it_runs_keeps_the_outcomes(self):
        self.assertEqual(self.outcomes("importside"), ([], 2, []))



# The second reviews' cases (2026-11-16, split-review2 A3): TestA and TestB
# (o_noise: TestC too), split one to a file (_ab2).
REVIEW2 = {
    'p_alias': '''"""Synthetic module p_alias (review case)."""
import unittest
class _Mix:
    seen = []
    def note(self, x):
        seen = self.seen
        seen += [x]
class TestA(_Mix, unittest.TestCase):
    def test_a(self):
        self.note(1)
class TestB(_Mix, unittest.TestCase):
    def test_every_noted_value_is_positive(self):
        for x in self.seen:
            self.assertGreater(x, 0)
''',
    'p_insort': '''"""Synthetic module p_insort (review case)."""
import unittest
import bisect
class _Mix:
    seen = []
class TestA(_Mix, unittest.TestCase):
    def test_a(self):
        bisect.insort(self.seen, 1)
class TestB(_Mix, unittest.TestCase):
    def test_every_noted_value_is_positive(self):
        for x in self.seen:
            self.assertGreater(x, 0)
''',
    'p_modheap': '''"""Synthetic module p_modheap (review case)."""
import unittest
import heapq
_LOG = []
class TestA(unittest.TestCase):
    def test_a(self):
        heapq.heappush(_LOG, 1)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
''',
    'p_modalias': '''"""Synthetic module p_modalias (review case)."""
import unittest
_LOG = []
class TestA(unittest.TestCase):
    def test_a(self):
        log = _LOG
        log += [1]
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
''',
    'p_modfn': '''"""Synthetic module p_modfn (review case)."""
import unittest
_LOG = []
def _log():
    return _LOG
class TestA(unittest.TestCase):
    def test_a(self):
        _log().append(1)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _log():
            self.assertGreater(x, 0)
''',
    'p_default': '''"""Synthetic module p_default (review case)."""
import unittest
def _seen(x=None, _all=[]):
    if x is not None:
        _all.append(x)
    return _all
class TestA(unittest.TestCase):
    def test_a(self):
        _seen(1)
class TestB(unittest.TestCase):
    def test_every_seen_value_is_positive(self):
        for x in _seen():
            self.assertGreater(x, 0)
''',
    'p_cache': '''"""Synthetic module p_cache (review case)."""
import unittest
import functools
@functools.lru_cache(maxsize=None)
def _made(key):
    return [key]
class TestA(unittest.TestCase):
    def test_a(self):
        _made(1).append(2)
class TestB(unittest.TestCase):
    def test_every_value_is_positive(self):
        for x in _made(1)[1:]:
            self.assertGreater(x, 0)
''',
    'p_subclasses': '''"""Synthetic module p_subclasses (review case)."""
import unittest
class _Base(unittest.TestCase):
    rate = 1
class TestA(_Base):
    rate = 5
    def test_a(self):
        pass
class TestB(_Base):
    def test_every_case_has_a_positive_rate(self):
        for c in _Base.__subclasses__():
            if c is not TestB:
                self.assertGreater(c.rate, 0)
''',
    'p_count2': '''"""Synthetic module p_count2 (review case)."""
import unittest
class _Mix:
    count = 0
    @classmethod
    def bump(cls):
        base = cls.__mro__[1]
        base.count = base.count + 1
class TestA(_Mix, unittest.TestCase):
    def test_a(self):
        self.bump()
class TestB(_Mix, unittest.TestCase):
    def test_b(self):
        self.assertGreaterEqual(self.count, 0)
''',
    'p_process': '''"""Synthetic module p_process (review case)."""
import unittest
import decimal
import os
from decimal import Decimal
class TestA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        decimal.getcontext().prec = 3
        os.environ["ZZ_STRICT"] = "1"
    def test_a(self):
        self.assertEqual(str(Decimal(1) / Decimal(3)), "0.333")
class TestB(unittest.TestCase):
    def test_a_third_rounds(self):
        third = Decimal(1) / Decimal(3)
        self.assertTrue(str(third).startswith("0.333"))
        if os.environ.get("ZZ_STRICT"):
            self.assertEqual(len(str(third)), 5)
''',
    'p_file': '''"""Synthetic module p_file (review case)."""
import unittest
from pathlib import Path
class TestA(unittest.TestCase):
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_no_sibling_module_lacks_unittest(self):
        for p in Path(__file__).parent.glob("test_zz_p_*.py"):
            self.assertIn("unittest", p.read_text())
''',
    'd_atexit': '''"""Synthetic module d_atexit (review case)."""
import unittest
import atexit
@atexit.register
def _bye():
    pass
def _unused():
    return 1
class _Field:
    def __set_name__(self, owner, name):
        owner.names = getattr(owner, "names", ()) + (name,)
__unittest = True
unittest.TestCase.maxDiff = None
class TestA(unittest.TestCase):
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_b(self):
        self.assertIsNone(self.maxDiff)
''',
    'alias2': '''"""Synthetic module alias2 (review case)."""
import unittest
class TestA(unittest.TestCase):
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_b(self):
        pass
Again = [TestA][0]
''',
    'made2': '''"""Synthetic module made2 (review case)."""
import unittest
def _make(n):
    class T(unittest.TestCase):
        def test_n(self):
            self.assertTrue(n)
    return T
Made1 = _make(1)
class TestA(unittest.TestCase):
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_b(self):
        pass
''',
    'made3': '''"""Synthetic module made3 (review case)."""
import unittest
def _make(n):
    class T(unittest.TestCase):
        def test_n(self):
            self.assertTrue(n)
    return T
TestsOfOne, tests_two = _make(1), _make(2)
class TestA(unittest.TestCase):
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_b(self):
        pass
''',
    'o_noise': '''"""Synthetic module o_noise (review case)."""
import unittest
import os
class TestA(unittest.TestCase):
    def test_a(self):
        os.environ["ZZ_ORDER2"] = "1"
class TestB(unittest.TestCase):
    def test_b(self):
        self.assertEqual(os.environ.get("ZZ_ORDER2"), "1")
class TestC(unittest.TestCase):
    def test_c(self):
        pass
''',
}
REVIEW2["p_getattr"] = REVIEW2["p_count2"].replace("cls.__mro__[1]", 'getattr(cls, "__mro__")[1]')
REVIEW2["p_mdefault"] = REVIEW2["p_alias"].replace("    def note(self, x):\n        seen = self.seen\n"
                                                   "        seen += [x]\n",
                                                   "    def note(self, x, seen=[]):\n"
                                                   "        seen.append(x)\n")
REVIEW2["p_keyword"] = '''"""Synthetic module p_keyword."""
import unittest
_LOG = []
def _note(rows):
    rows.append(1)
class TestA(unittest.TestCase):
    def test_a(self):
        _note(rows=_LOG)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
'''
REVIEW2["p_item"] = REVIEW2["p_keyword"].replace("_LOG = []\ndef _note(rows):\n    rows.append(1)\n",
                                               '_LOG = {"a": []}\n').replace(
    "        _note(rows=_LOG)\n", '        _LOG["a"].append(1)\n').replace("for x in _LOG:",
                                                                         'for x in _LOG["a"]:')
REVIEW2["p_held"] = REVIEW2["p_item"].replace('_LOG = {"a": []}', "_LOG = []").replace(
    '        _LOG["a"].append(1)\n', "        pair = (_LOG, 2)\n        pair[0].append(1)\n").replace(
    'for x in _LOG["a"]:', "for x in _LOG:")
REVIEW2["both"] = REVIEW2["p_alias"].replace("        for x in self.seen:\n",
                                             "        for x in self.seen + [__file__]:\n")
# A copy kept by another object, a copy's changing method handed out.
REVIEW2["p_kept"] = '''"""Synthetic module p_kept."""
import unittest
_LOG = []
_ALL = []
class TestA(unittest.TestCase):
    def test_a(self):
        _ALL.append(_LOG)
        _ALL[0].append(1)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
'''
REVIEW2["p_method"] = REVIEW2["p_kept"].replace("_ALL = []\n", "").replace(
    "        _ALL.append(_LOG)\n        _ALL[0].append(1)\n", "        self.addCleanup(_LOG.clear)\n")
# a callee the text does not show changes a copy: the outcome run sees it
REVIEW2["p_helper"] = REVIEW2["p_modheap"].replace("import heapq", "from _style import push")\
    .replace("heapq.heappush(", "push(")
REVIEW2["o_quiet"] = REVIEW2["o_noise"].replace(
    'self.assertEqual(os.environ.get("ZZ_ORDER2"), "1")',
    'self.assertEqual(os.environ.pop("ZZ_ORDER2", None), "1")')
# Read only, in every way the mover lets a copy be read.
REVIEW2["readings"] = '''"""Copied values only read."""
import unittest

import os
import subprocess

from _style import CapturedWidth, scan

_WIDTH = CapturedWidth()
_ENV = dict(os.environ, ZZ_A="1")
_ROWS = [1, 2]
_CASES = {"a": (1, 2)}
_ALSO = _ROWS


def setUpModule():
    _WIDTH.start()


def _total(rows):
    return sum(rows)


def _fresh():
    _CASES = {}                 # its own, not the module's
    _CASES["x"] = 1
    return _CASES


class _Mix:
    seen = []
    dates = dict(a="2026-01-01")

    def check(self, rows):
        self.assertEqual(len(rows), len(self.seen) + 2)


class TestA(_Mix, unittest.TestCase):
    def test_a(self):
        self.check(_ROWS)
        self.assertEqual(sorted(_ROWS), [*_ROWS])
        self.assertIn(1, _ROWS)
        self.assertEqual(_total(_ROWS + [3]), 6)
        self.assertEqual(f"{_CASES['a']}", "(1, 2)")
        self.assertIs(_ALSO, _ROWS)
        self.assertIn("ZZ_A", _ENV)
        self.assertEqual(_fresh(), {"x": 1})
        self.assertEqual(_CASES.get("a"), _CASES["a"])
        self.assertEqual(dict(**self.dates), {**self.dates})
        for x in self.seen:
            self.fail(x)


class TestB(_Mix, unittest.TestCase):
    def test_b(self):
        self.assertEqual(list(_ALSO), _ROWS[:2])
        self.assertTrue(_ROWS and not self.seen)
        self.assertEqual(_ENV["ZZ_A"], "1")
        # a callee the text does not show: the outcome run records it
        self.assertEqual(scan(_ROWS, dates=self.dates), 2)
        subprocess.run(["true"], env=_ENV, check=False)
        os.environ.update(_ENV)
        got = []
        got.extend(_ROWS)
        self.assertNotIn("b", _CASES)
        self.assertEqual(_total(_ROWS), 3)
'''


def _ab2(name):
    if name in ("o_noise", "o_quiet"):
        return [(f"test_fix_zz_{name}::TestA", ALPHA), (f"test_fix_zz_{name}::TestB", ALPHA),
                (f"test_fix_zz_{name}::TestC", BETA)]
    return [(f"test_fix_zz_{name}::TestA", ALPHA), (f"test_fix_zz_{name}::TestB", BETA)]


def _module2(name):
    return {f"tests/test_fix_zz_{name}.py": REVIEW2[name]}


class TestTheSecondReviewsCases(_SplitCase):
    def test_state_a_copy_would_hold_is_refused_with_its_line(self):
        cases = {
            "p_alias": "_Mix (line 3) goes into 2 new files and has a class attribute that is a "
                       "list (seen, line 4), aliased at line 6",
            "p_insort": "_Mix (line 4) goes into 2 new files and has a class attribute that is a "
                        "list (seen, line 5), passed to insort() at line 8",
            "p_modheap": "_LOG (line 4) goes into 2 new files and is passed to heappush() at line 7",
            "p_modalias": "_LOG (line 3) goes into 2 new files and is aliased at line 6",
            "p_modfn": "_LOG (line 3) goes into 2 new files and is handed out at line 5",
            "p_kept": "_LOG (line 3) goes into 2 new files and is kept by .append() at line 7",
            "p_keyword": "_LOG (line 3) goes into 2 new files and is passed to _note() at line 8, "
                         "which changes an argument",
            "p_item": "_LOG (line 3) goes into 2 new files and is changed at line 6 (.append())",
            "p_held": "_LOG (line 3) goes into 2 new files and is aliased at line 6",
            "p_method": "_LOG (line 3) goes into 2 new files and is its .clear() handed out at "
                        "line 6",
            "p_default": "_seen (line 3) goes into 2 new files and has a mutable default (a list, "
                         "line 3)",
            "p_cache": "_made (line 5) goes into 2 new files and has a cache "
                       "(functools.lru_cache(maxsize=None), line 4)",
            "p_subclasses": "line 11 reads __subclasses__ (a class's subclasses: a file's copy has "
                            "its own file's only), which a split changes: by hand first",
            "p_count2": "line 7 reads __mro__ (a class's bases, through which a copied base's "
                        "state is changed), which a split changes",
            "p_getattr": "line 7 reads __mro__ (a class's bases",
            "p_mdefault": "note (line 5) goes into 2 new files and has a mutable default (a list, "
                          "line 5)",
            "p_file": "line 9 reads __file__, where the module is, which a split moves (a path "
                      "from it changes with the new file's folder): name the folder through "
                      "tests/_paths.py",
            "made2": "line 8: Made1 is made by a call (_make(1)), a test class no line can place",
            "made3": "line 8: TestsOfOne, tests_two is made by a call ((_make(1), _make(2))), a "
                     "test class no line can place",
        }
        for name, why in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, {**SPLIT_EXTRA, **_module2(name)})
                with self.assertRaises(SystemExit) as cm, _ruff(), redirect_stdout(io.StringIO()):
                    MT.move(r, _ab2(name))
                self.assertIn(f"tests/test_fix_zz_{name}.py: {why}", str(cm.exception))
                self.assertTrue((r / "tests" / f"test_fix_zz_{name}.py").is_file())

    def test_a_copy_only_read_is_split(self):
        style = {"tests/_style.py": '"""S."""\n\n\nclass CapturedWidth:\n    def start(self):\n'
                                    '        pass\n\n\ndef scan(rows, dates):\n    return len(rows)\n'}
        r, _tree, _head, base, _h = self.split(place=_ab2("readings"),
                                               extra={**_module2("readings"), **style})
        plan = MT.SplitPlan(base, "test_fix_zz_readings", _places2("readings"))
        self.assertEqual(sorted(plan.shared_names()),
                         ["_ALSO", "_CASES", "_ENV", "_Mix", "_ROWS", "_WIDTH", "_total",
                          "setUpModule"])
        for f in (ALPHA, BETA):
            self.assertIn("\n_ROWS = [1, 2]\n", (r / "tests" / f).read_text())

    def test_every_refusal_at_once(self):
        both = REVIEW2["both"]
        gamma, delta = "features/misc/test_zz_gamma.py", "rules/ca/sl/test_zz_delta.py"
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_zz_both.py": both,
                           **_module2("p_modheap")})
            (r / "tests" / "features" / "misc").mkdir(parents=True)
            for d in ("features", "features/misc"):
                (r / "tests" / d / "__init__.py").write_text('"""P."""\n')
            (r / "tests" / gamma).write_text('"""Taken."""\n')
            with self.assertRaises(SystemExit) as cm, _ruff(), redirect_stdout(io.StringIO()):
                MT.move(r, [("test_fix_zz_both::TestA", ALPHA), ("test_fix_zz_both::TestB", BETA),
                            ("test_fix_zz_p_modheap::TestA", gamma),
                            ("test_fix_zz_p_modheap::TestB", delta)])
        lines = str(cm.exception).splitlines()
        self.assertEqual([ln.split(": ")[1] for ln in lines], [
            f"tests/{gamma} exists", f"tests/{gamma}",       # and its name is taken
            "tests/test_fix_zz_both.py", "tests/test_fix_zz_both.py",
            "tests/test_fix_zz_p_modheap.py"])
        self.assertIn("line 13 reads __file__", lines[2])
        self.assertIn("_Mix (line 3) goes into 2 new files", lines[3])
        self.assertIn("_LOG (line 4) goes into 2 new files", lines[4])

    def test_the_verifier_names_every_refusal_of_a_split_by_hand(self):
        with mock.patch.object(MT.SplitPlan, "_shared_state", lambda *a: None), \
                mock.patch.object(MT.SplitPlan, "_links", lambda *a: None):
            got = self.split(place=[("test_fix_zz_both::TestA", ALPHA),
                                    ("test_fix_zz_both::TestB", BETA)],
                             extra={"tests/test_fix_zz_both.py": REVIEW2["both"]})
        problems, _listed = self.verify_text(*got)
        head = ("text: BASE's mover refuses the split HEAD's class lines make: "
                "tests/test_fix_zz_both.py: ")
        for why in ("line 13 reads __file__", "_Mix (line 3) goes into 2 new files"):
            self.assertTrue(any(p.startswith(head + why) for p in problems), problems)

    def test_an_unread_decorated_definition_goes_into_every_file(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, **_module2("d_atexit")})
            plan = MT.SplitPlan(r, "test_fix_zz_d_atexit", _places2("d_atexit"))
        for f in (ALPHA, BETA):
            self.assertIn("\nimport atexit\n"
                          "@atexit.register\ndef _bye():\n", plan.texts[f])
            self.assertNotIn("_unused", plan.texts[f])
        self.assertIn("  in every file for what it runs at import: line 5 (_bye) (a decorated "
                      "definition no statement reads: its decorator runs at import)",
                      plan.describe())
        self.assertIn("  in no new file: line 7 (_unused)", plan.describe())

    def test_a_run_files_comment_is_listed_to_settle(self):
        self.assertEqual(MT.comment_columns("a.sh", 'echo "a # b" x  # c\nx#y\n# z\n'),
                         {1: 16, 3: 0})
        self.assertEqual(MT.comment_columns("a.yml", "k: 'v # 1' # c\n"), {1: 11})
        self.assertEqual(MT.comment_columns("a.py", 'X = "#"  # c\nY = 1# d\n'), {1: 9, 2: 5})
        yml = "on: push\n# the image: tests/test_fix_split.py checks it\n"
        got = self.split(extra={".github/workflows/tests.yml": yml,
                                "scripts/ci.sh": "run test_fix_demo  # not test_fix_split\n"})
        self.assertLessEqual({".github/workflows/tests.yml:2", "scripts/ci.sh:1"},
                             {x.split(": ")[0] for x in self.left})
        problems, _listed = self.verify_text(*got)
        self.assertEqual(problems, [])
        # In code it is refused, a comment after it or not.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "scripts/ci.sh": "run test_fix_split  # its tests\n"})
            self.assertEqual(MT.run_file_problems(r, "test_fix_split", ["TestA", "TestB", "TestC"]), [
                "scripts/ci.sh:1 names it as a module, which a split leaves no more: name a class "
                "of it there (`test_fix_split.Class`, which follows its class) or another module, "
                "by hand first"])

    def test_the_gate_tests_stems_are_said_what_to_do(self):
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_1029_devtools.py":
                           '"""Gate."""\nSTEMS = ("test_fix_demo",\n         "test_fix_split")\n'})
            self.assertEqual(MT.run_file_problems(r, "test_fix_split", ["TestA", "TestB", "TestC"]), [
                "tests/test_fix_1029_devtools.py:3 names it by its stem, as a stage's grep picks "
                "a module: drop the stem there in a commit before the split, and name the new "
                "modules' stems the stage picks (the dry run lists the ones that leave it) in one "
                "after it"])


def _places2(name):
    return [(o.split("::")[1], n) for o, n in _ab2(name)]


class TestTheSecondReviewsOutcomes(_SplitCase):
    def outcomes(self, name, patches=()):
        with contextlib.ExitStack() as stack:
            for target, value in patches:
                stack.enter_context(mock.patch.object(MT.SplitPlan, target, value))
            _r, _tree, _head, base, head = self.split(place=_ab2(name), extra=_module2(name))
            plan = MT.SplitPlan(base, f"test_fix_zz_{name}", _places2(name))
        mods, classes = VT.batch_moves(base, head)
        with tempfile.TemporaryDirectory() as td:
            return VT.outcome_problems(base, head, {f"test_fix_zz_{name}": plan}, mods, classes,
                                       Path(td))

    def test_process_state_one_file_leaves_another_is_seen_by_its_test(self):
        # The decimal context and os.environ TestA's setUpClass leaves: TestB
        # takes another branch, reads another context, and its local holds
        # another value.
        problems, n, _notes = self.outcomes("p_process")
        self.assertEqual(n, 2)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(
            f"outcomes: tests/test_fix_zz_p_process.py: {MT.dotted(BETA)}.TestB.test_a_third_rounds "
            f"does or checks otherwise after the split: TestB.test_a_third_rounds line +4 ran 1 "
            f"time(s) at BASE, 0 after; assertion #2 at BASE, assertEqual at "
            f"TestB.test_a_third_rounds line +4, is not made after; the decimal context holds "
            f"another value at its start; the environment's ZZ_STRICT is unset at its start; "
            f"TestB.test_a_third_rounds's local third differs"), problems)

    def test_a_copys_state_changed_after_import_is_seen_by_the_run(self):
        # The mover's static refusals switched off: the recorded run is the guard.
        off = (("_shared_state", lambda *a: None),)
        for name in ("p_alias", "p_modheap", "p_default", "p_cache"):
            with self.subTest(name):
                problems, _n, _notes = self.outcomes(name, off)
                self.assertTrue(any(p.startswith(
                    f"outcomes: tests/test_fix_zz_{name}.py: {MT.dotted(BETA)}.TestB.") and
                    "does or checks otherwise after the split: " in p for p in problems), problems)

    def test_a_copy_a_callee_changes_is_refused_by_the_run(self):
        style = {"tests/_style.py": '"""S."""\n\n\ndef push(rows, x):\n    rows.append(x)\n'}
        with mock.patch.dict(SPLIT_EXTRA, style):
            problems, _n, _notes = self.outcomes("p_helper")
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(
            f"outcomes: tests/test_fix_zz_p_helper.py: {MT.dotted(BETA)}.TestB.test_every_logged_value"
            f"_is_positive does or checks otherwise after the split: "), problems)

    def test_state_no_test_reads_refuses_nothing(self):
        # o_noise: TestA leaves an environment variable that TestC, in another
        # file, never reads (round 3's watch refused it).
        self.assertEqual(self.outcomes("o_quiet"), ([], 3, []))
        self.assertEqual(self.outcomes("o_noise"), ([], 3, []))

    def test_a_test_class_under_two_names_is_counted(self):
        problems, _n, _notes = self.outcomes("alias2")
        self.assertEqual(problems, [
            "outcomes: tests/test_fix_zz_alias2.py: the ids: 1 of BASE's (renamed) missing, 0 new: "
            "['features.misc.test_zz_alpha.TestA.test_a']"])

    def test_a_class_made_by_a_call_is_caught_by_the_run(self):
        problems, _n, _notes = self.outcomes("made2", (("_makes_test_case", lambda *a: False),))
        self.assertIn("outcomes: tests/test_fix_zz_made2.py: tests of _make.<locals>.T, a class "
                      "no line places (made by a call, or bound so): write it as a class of its "
                      "own by hand first", problems)

    def test_an_unread_decorated_definition_keeps_the_outcomes(self):
        self.assertEqual(self.outcomes("d_atexit"), ([], 2, []))



# The third review's cases (2026-11-17, split-review3 A/mods): TestA changes
# what TestB reads; each split one class to a file (_ab3). Each must be
# refused by the mover or fail the verifier.
REVIEW3 = {
    'n_argv': '''"""Synthetic module n_argv (review case)."""
import unittest
import sys
class TestA(unittest.TestCase):
    def test_a(self):
        sys.argv.append("--zz")
class TestB(unittest.TestCase):
    def test_b(self):
        if "--zz" in sys.argv:
            self.assertEqual(sys.argv.count("--zz"), 1)
''',
    'n_callee': '''"""Synthetic module n_callee (review case)."""
import unittest
from _support.zz_add2 import add
_LOG = []
class TestA(unittest.TestCase):
    def test_a(self):
        add(_LOG, 1)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
''',
    'n_closure': '''"""Synthetic module n_closure (review case)."""
import unittest
def _counter():
    n = []
    def add(x=None):
        if x is not None:
            n.append(x)
        return tuple(n)
    return add
_seen = _counter()
class TestA(unittest.TestCase):
    def test_a(self):
        _seen(1)
class TestB(unittest.TestCase):
    def test_every_seen_value_is_positive(self):
        for x in _seen():
            self.assertGreater(x, 0)
''',
    'n_etree': '''"""Synthetic module n_etree (review case)."""
import unittest
import xml.etree.ElementTree as ET
_ROOT = ET.Element("rows")
class TestA(unittest.TestCase):
    def test_a(self):
        ET.SubElement(_ROOT, "row")
class TestB(unittest.TestCase):
    def test_every_child_is_a_row(self):
        for e in _ROOT:
            self.assertEqual(e.tag, "row")
''',
    'n_file': '''"""Synthetic module n_file (review case)."""
import unittest
import tempfile
_OUT = tempfile.TemporaryFile("w+")
class TestA(unittest.TestCase):
    def test_a(self):
        print("1", file=_OUT)
class TestB(unittest.TestCase):
    def test_every_line_is_a_digit(self):
        _OUT.seek(0)
        for line in _OUT:
            self.assertTrue(line.strip().isdigit())
''',
    'n_fnattr': '''"""Synthetic module n_fnattr (review case)."""
import unittest
def _note(x=None):
    if x is not None:
        _note.seen += (x,)
    return _note.seen
_note.seen = ()
class TestA(unittest.TestCase):
    def test_a(self):
        _note(1)
class TestB(unittest.TestCase):
    def test_every_noted_value_is_positive(self):
        for x in _note():
            self.assertGreater(x, 0)
''',
    'n_global': '''"""Synthetic module n_global (review case)."""
import unittest
_N = 0
class TestA(unittest.TestCase):
    def test_a(self):
        global _N
        _N += 1
class TestB(unittest.TestCase):
    def test_b(self):
        if _N:
            self.assertEqual(_N, 1)
''',
    'n_global2': '''"""Synthetic module n_global2 (review case)."""
import unittest
_N = 0
class TestA(unittest.TestCase):
    def test_a(self):
        global _N
        _N = 1
class TestB(unittest.TestCase):
    def test_b(self):
        if _N:
            self.assertEqual(_N, 1)
''',
    'n_global3': '''"""Synthetic module n_global3 (review case)."""
import unittest
class TestA(unittest.TestCase):
    def test_a(self):
        global _ROWS
        _ROWS = [1]
class TestB(unittest.TestCase):
    def test_every_row_is_positive(self):
        for x in globals_rows():
            self.assertGreater(x, 0)
def globals_rows():
    try:
        return _ROWS
    except NameError:
        return []
''',
    'n_globals': '''"""Synthetic module n_globals (review case)."""
import unittest
class TestA(unittest.TestCase):
    def test_a(self):
        globals()["_FLAG"] = 1
class TestB(unittest.TestCase):
    def test_b(self):
        if globals().get("_FLAG"):
            self.assertEqual(1 + 1, 2)
''',
    'n_hash': '''"""Synthetic module n_hash (review case)."""
import unittest
import hashlib
_H = hashlib.sha1()
_EMPTY = hashlib.sha1().hexdigest()
class TestA(unittest.TestCase):
    def test_a(self):
        (lambda h: h.update(b"x"))(_H)
class TestB(unittest.TestCase):
    def test_b(self):
        if (lambda h: h.hexdigest())(_H) != _EMPTY:
            self.assertEqual(len((lambda h: h.hexdigest())(_H)), 40)
''',
    'n_home': '''"""Synthetic module n_home (review case)."""
import unittest
from pathlib import Path
class TestA(unittest.TestCase):
    def test_a(self):
        Path.home().joinpath("zz_a.txt").write_text("1")
class TestB(unittest.TestCase):
    def test_every_file_written_holds_a_digit(self):
        for p in Path.home().glob("zz_*.txt"):
            self.assertTrue(p.read_text().isdigit())
''',
    'n_impclass': '''"""Synthetic module n_impclass (review case)."""
import unittest
class TestA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        unittest.TestCase.zz_strict = True
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_strict_mode_checks(self):
        if getattr(self, "zz_strict", False):
            self.assertEqual(1 + 1, 2)
''',
    'n_iter': '''"""Synthetic module n_iter (review case)."""
import unittest
_IT = iter([1, 2, 3])
class TestA(unittest.TestCase):
    def test_a(self):
        self.assertEqual(next(_IT), 1)
class TestB(unittest.TestCase):
    def test_the_rest_is_above_one(self):
        for x in _IT:
            self.assertGreater(x, 0)
''',
    'n_logging': '''"""Synthetic module n_logging (review case)."""
import unittest
import logging
class TestA(unittest.TestCase):
    def test_a(self):
        logging.getLogger("zz.review").addHandler(logging.NullHandler())
class TestB(unittest.TestCase):
    def test_every_handler_is_a_null_handler(self):
        for h in logging.getLogger("zz.review").handlers:
            self.assertIsInstance(h, logging.NullHandler)
''',
    'n_memo': '''"""Synthetic module n_memo (review case)."""
import unittest
from taxjson.engine import rows
class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(rows._is_iso_date("2024-01-02"))
class TestB(unittest.TestCase):
    def test_a_cached_date_is_answered_from_the_cache(self):
        if rows._is_iso_date.cache_info().currsize:
            before = rows._is_iso_date.cache_info().hits
            rows._is_iso_date("2024-01-02")
            self.assertEqual(rows._is_iso_date.cache_info().hits, before + 1)
''',
    'n_modattr': '''"""Synthetic module n_modattr (review case)."""
import unittest
import sys
class TestA(unittest.TestCase):
    def test_a(self):
        sys.modules[type(self).__module__].zz_flag = 1
class TestB(unittest.TestCase):
    def test_b(self):
        if getattr(sys.modules[type(self).__module__], "zz_flag", 0):
            self.assertEqual(1 + 1, 2)
''',
    'n_patch': '''"""Synthetic module n_patch (review case)."""
import unittest
from unittest import mock
from taxjson.engine import rows
class TestA(unittest.TestCase):
    def test_a(self):
        mock.patch.object(rows, "_is_iso_date", lambda v: False).start()
        self.assertFalse(rows._is_iso_date("2024-01-02"))
class TestB(unittest.TestCase):
    def test_a_real_date_is_iso(self):
        if rows._is_iso_date("2024-01-02"):
            self.assertTrue(rows._is_iso_date("2024-02-29"))
''',
    'n_placed': '''"""Synthetic module n_placed (review case)."""
import unittest
class TestA(unittest.TestCase):
    def test_a(self):
        TestB.strict = True
class TestB(unittest.TestCase):
    strict = False
    def test_b(self):
        if self.strict:
            self.assertEqual(1 + 1, 2)
''',
    'n_random': '''"""Synthetic module n_random (review case)."""
import unittest
import random
class TestA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        random.seed(7)
    def test_a(self):
        pass
class TestB(unittest.TestCase):
    def test_b(self):
        self.assertLess(random.random(), 1)
''',
    'n_setup': '''"""Synthetic module n_setup (review case)."""
import unittest
import tempfile
from pathlib import Path
_DIR = None
def setUpModule():
    global _DIR
    _DIR = Path(tempfile.mkdtemp())
class TestA(unittest.TestCase):
    def test_a(self):
        (_DIR / "a.txt").write_text("1")
class TestB(unittest.TestCase):
    def test_every_file_written_holds_a_digit(self):
        for p in _DIR.glob("*.txt"):
            self.assertTrue(p.read_text().isdigit())
''',
    'n_skip': '''"""Synthetic module n_skip (review case)."""
import unittest
import os
from _support.zz_add import add
_LOG = []
@unittest.skipUnless(os.environ.get("ZZ_RUN_A"), "needs ZZ_RUN_A")
class TestA(unittest.TestCase):
    def test_a(self):
        add(_LOG, 1)
class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        for x in _LOG:
            self.assertGreater(x, 0)
''',
    'n_support': '''"""Synthetic module n_support (review case)."""
import unittest
from _support import zz_state
class TestA(unittest.TestCase):
    def test_a(self):
        zz_state.note(1)
class TestB(unittest.TestCase):
    def test_every_noted_value_is_positive(self):
        for x in zz_state.LOG:
            self.assertGreater(x, 0)
''',
    'n_sysmod': '''"""Synthetic module n_sysmod (review case)."""
import unittest
import sys
import types
class TestA(unittest.TestCase):
    def test_a(self):
        m = types.ModuleType("zz_fake_mod")
        m.rows = [1]
        sys.modules["zz_fake_mod"] = m
class TestB(unittest.TestCase):
    def test_every_row_is_positive(self):
        for x in getattr(sys.modules.get("zz_fake_mod"), "rows", []):
            self.assertGreater(x, 0)
''',
    'n_tmp': '''"""Synthetic module n_tmp (review case)."""
import unittest
import tempfile
from pathlib import Path
_TMP = tempfile.mkdtemp()
class TestA(unittest.TestCase):
    def test_a(self):
        Path(_TMP, "a.txt").write_text("1")
class TestB(unittest.TestCase):
    def test_every_file_written_holds_a_digit(self):
        for p in Path(_TMP).glob("*.txt"):
            self.assertTrue(p.read_text().isdigit())
''',
    'n_warn': '''"""Synthetic module n_warn (review case)."""
import unittest
import warnings
class TestA(unittest.TestCase):
    def test_a(self):
        warnings.simplefilter("error", ResourceWarning)
class TestB(unittest.TestCase):
    def test_b(self):
        self.assertTrue(warnings.filters)
''',
}
# Their helpers below tests/_support/ (shared by import, one per process).
REVIEW3_SUPPORT = {
    'tests/_support/zz_add2.py': '"""Review helper: add to a list."""\n\n\ndef add(rows, x):\n    rows.append(x)\n',
    'tests/_support/zz_add.py': '"""Review helper: add to a list."""\n\n\ndef add(rows, x):\n    rows.append(x)\n',
    'tests/_support/zz_state.py': '"""Review helper: a log."""\nLOG = []\n\n\ndef note(x):\n    LOG.append(x)\n',
}
# The critique's cases (2026-11-17): a value that reaches TestB's assertion
# only through C, or changes while its check does not; a child that reads
# what another class left; a product branch another class's leak opened.
# The mover lets each through: the verifier must fail it.
CRITIQUE = {
    "c_seen": '''"""A helper's list another class fills, checked through C."""
import unittest

from _support import zz_seen


class TestA(unittest.TestCase):
    def test_a(self):
        zz_seen.note("1")


class TestB(unittest.TestCase):
    def test_every_seen_value_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, zz_seen.SEEN)))
''',
    "c_subset": '''"""A copied list a callee the mover cannot read fills; a subset check."""
import unittest

from _support.zz_add3 import add

LOG = []
ALLOWED = {"a", "b"}


class TestA(unittest.TestCase):
    def test_a(self):
        add(LOG, "a")


class TestB(unittest.TestCase):
    def test_every_logged_value_is_allowed(self):
        self.assertTrue(set(LOG) <= ALLOWED)
''',
    "c_genexpr": '''"""The same list, checked through a generator."""
import unittest

from _support.zz_add3 import add

LOG = []


class TestA(unittest.TestCase):
    def test_a(self):
        add(LOG, 1)


class TestB(unittest.TestCase):
    def test_every_logged_value_is_positive(self):
        self.assertTrue(all(x > 0 for x in LOG))
''',
    "c_decimal": '''"""The decimal precision one class leaves: another's value, the same check."""
import decimal
import unittest
from decimal import Decimal


class TestA(unittest.TestCase):
    def test_a(self):
        decimal.getcontext().prec = 50
        self.assertEqual(Decimal(1) / Decimal(4), Decimal("0.25"))


class TestB(unittest.TestCase):
    def test_a_third_starts_with_threes(self):
        s = str(Decimal(1) / Decimal(3))
        self.assertTrue(s.startswith("0.333"))
''',
    "c_child": '''"""A child process that reads a file another class wrote under HOME."""
import subprocess
import sys
import unittest
from pathlib import Path

_PROBE = "import pathlib; print((pathlib.Path.home() / 'zz_flag.txt').exists())"


class TestA(unittest.TestCase):
    def test_a(self):
        Path.home().joinpath("zz_flag.txt").write_text("1")


class TestB(unittest.TestCase):
    def test_the_child_runs(self):
        self.assertEqual(subprocess.run([sys.executable, "-c", _PROBE],
                                        capture_output=True).returncode, 0)
''',
    "c_product": '''"""An environment variable one class leaves opens a product branch."""
import os
import unittest

import zzprod


class TestA(unittest.TestCase):
    def test_a(self):
        os.environ["ZZ_PROD_VERBOSE"] = "1"


class TestB(unittest.TestCase):
    def test_the_report_is_made(self):
        self.assertTrue(zzprod.report())
''',
}
CRITIQUE_EXTRA = {
    "tests/_support/zz_seen.py": '"""Critique helper: a list."""\nSEEN = []\n\n\n'
                                 'def note(x):\n    SEEN.append(x)\n',
    "tests/_support/zz_add3.py": '"""Critique helper: add to a list."""\n\n\n'
                                 'def add(rows, x):\n    rows.append(x)\n',
    "src/zzprod/__init__.py": '"""A product stand-in."""\nimport os\n\n\ndef report():\n'
                              '    if os.environ.get("ZZ_PROD_VERBOSE"):\n'
                              '        print("verbose")\n    return True\n',
}
# Clean splits: helpers copied, module fixtures, a threaded test, temp
# folders, a value naming OLD (`<MOD>` at BASE and after). Each must pass.
CLEAN = {
    "k_named": '''"""A value that holds the module's old name."""
import unittest

_LOG = "logs/test_fix_zz_k_named.txt"


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(_LOG.endswith(".txt"))


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertIn("logs/", _LOG)
''',
    "k_helpers": '''"""A helper and a constant both classes read."""
import unittest

_ROWS = (1, 2, 3)


def _total(rows):
    return sum(rows)


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertEqual(_total(_ROWS), 6)


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertEqual(_total(_ROWS[:2]), 3)
''',
    "k_fixtures": '''"""Module and class fixtures."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_STATE = {}


def setUpModule():
    _STATE["dir"] = tempfile.mkdtemp()


def tearDownModule():
    shutil.rmtree(_STATE.pop("dir"))


class TestA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(_STATE["dir"], "a.txt")
        cls.path.write_text("a")

    @classmethod
    def tearDownClass(cls):
        cls.path.unlink()

    def test_a(self):
        self.assertEqual(self.path.read_text(), "a")


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertTrue(os.path.isdir(_STATE["dir"]))
        self.assertEqual(os.listdir(_STATE["dir"]), [])
''',
    "k_thread": '''"""A thread whose loop runs as often as the clock lets it."""
import threading
import time
import unittest


class TestA(unittest.TestCase):
    def test_a_worker_spins(self):
        n = [0]
        end = time.perf_counter() + 0.02

        def spin():
            while time.perf_counter() < end:
                n[0] += 1
        t = threading.Thread(target=spin)
        t.start()
        t.join()
        self.assertGreater(n[0], 0)


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertEqual(sum([1, 1]), 2)
''',
    "k_temp": '''"""Temp folders and their names."""
import os
import tempfile
import unittest
from pathlib import Path


class TestA(unittest.TestCase):
    def test_a_temp_file_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.txt"
            p.write_text("x")
            self.assertEqual(os.listdir(d), ["out.txt"])
            self.assertEqual(str(p), os.path.join(d, "out.txt"))
            name = os.path.basename(d)
            self.assertTrue(name.startswith("tmp"))


class TestB(unittest.TestCase):
    def test_b(self):
        d = tempfile.mkdtemp()
        self.assertTrue(os.path.isdir(d))
        os.rmdir(d)
''',
    # maps keyed by id(), as the brokerages' row lines and IB's trade
    # metadata (in a dataclass) are: the ids differ in each run, the
    # digests do not
    "k_ids": '''"""Maps keyed by id()."""
import dataclasses
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(True)


class TestB(unittest.TestCase):
    def test_rows_keep_their_lines(self):
        @dataclasses.dataclass
        class Parse:
            meta: dict

        rows = [{"n": i} for i in range(3)]
        lines = {id(r): (r, i + 2) for i, r in enumerate(rows)}
        parse = Parse({id(r): {"t": r, "fee": 0} for r in rows})
        self.assertEqual(sorted(v[1] for v in lines.values()), [2, 3, 4])
        self.assertEqual(len(parse.meta), 3)
''',
    # a product module only TestA's file imports, a name TestB also uses:
    # not loaded at TestB's start after the split, so not compared
    "k_unloaded": '''"""A product module one class imports."""
import unittest

import zzlimits


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertEqual(zzlimits.MAX_ROWS, 3)


class TestB(unittest.TestCase):
    def test_b(self):
        limits = {"MAX_ROWS": 2}
        self.assertLess(limits["MAX_ROWS"], 3)
''',
}
CLEAN_EXTRA = {"src/zzlimits/__init__.py": '"""A product stand-in: a limit."""\nMAX_ROWS = 3\n'}


def _ab3(name):
    return [(f"test_fix_zz_{name}::TestA", f"features/misc/test_zz_{name}_a.py"),
            (f"test_fix_zz_{name}::TestB", f"rules/ca/sl/test_zz_{name}_b.py")]


class _ReviewCase(_SplitCase):
    def run_cases(self, sources, extra=None, patches=(), edit=None):
        """SOURCES (name -> module) in one synthetic repository, each split
        one class to a file where the mover lets it (PATCHES on SplitPlan
        or MT, the mover's rules off; EDIT on HEAD's tree): (refused: name
        -> why, the outcome problems of the rest, the notes)."""
        files = {f"tests/test_fix_zz_{n}.py": s for n, s in sources.items()}
        with contextlib.ExitStack() as stack:
            for where, target, value in patches:
                stack.enter_context(mock.patch.object(where, target, value))
            with tempfile.TemporaryDirectory() as td:
                r = Path(td)
                _synthetic(r, {**SPLIT_EXTRA, **files, **(extra or {})})
                refused, place = {}, []
                for name in sources:
                    try:
                        MT.SplitPlan(r, f"test_fix_zz_{name}",
                                     [(o.split("::")[1], n) for o, n in _ab3(name)])
                        place += _ab3(name)
                    except SystemExit as e:
                        refused[name] = str(e)
            if not place:
                return refused, [], []
            _r, _tree, _head, base, head = self.split(place=place, extra={**files, **(extra or {})},
                                                      edit=edit)
            plans = {o: MT.SplitPlan(base, o, [(x.split("::")[1], n) for x, n in place
                                               if x.startswith(f"{o}::")])
                     for o in dict.fromkeys(x.split("::")[0] for x, _n in place)}
        mods, classes = VT.batch_moves(base, head)
        with tempfile.TemporaryDirectory() as td:
            problems, _n, notes = VT.outcome_problems(base, head, plans, mods, classes, Path(td))
        return refused, problems, notes


class TestTheThirdReviewsCases(_ReviewCase):
    def test_each_case_is_refused_or_fails(self):
        refused, problems, _notes = self.run_cases({**REVIEW3, **CRITIQUE},
                                                   {**REVIEW3_SUPPORT, **CRITIQUE_EXTRA})
        # The mover: a `global` name read and written (n_global, whose copy
        # went only where it is read), one a `global` makes, a copied temp
        # folder or open file; and round 2's rules.
        self.assertEqual(sorted(refused), ["n_closure", "n_file", "n_fnattr", "n_global",
                                           "n_global2", "n_global3", "n_globals", "n_modattr",
                                           "n_placed", "n_tmp"])
        for name, why in {
                "n_global": "test_a (line 5) changes _N after import, which more than one new "
                            "file reaches",
                "n_global2": "test_a (line 5) changes _N after import",
                "n_global3": "_ROWS, a module-level name a `global` statement makes when it runs "
                             "(line 5), is set or read in 2 new files",
                "n_tmp": "_TMP (line 5) goes into 2 new files and holds a temp folder "
                         "(tempfile.mkdtemp(), line 5): each file would make its own",
                "n_file": "_OUT (line 4) goes into 2 new files and holds an open file "
                          "(tempfile.TemporaryFile(), line 4)"}.items():
            self.assertIn(f"tests/test_fix_zz_{name}.py: {why}", refused[name])
        # The verifier: every other case, named.
        for name in sorted((set(REVIEW3) | set(CRITIQUE)) - set(refused)):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        said = "\n".join(problems)
        b = "rules.ca.sl.test_zz_{}_b.TestB.{} does or checks otherwise after the split: "
        for name, test, why in (
                # values read at the test's start, through C
                ("c_seen", "test_every_seen_value_is_a_digit",
                 "_support.zz_seen.SEEN holds another value at its start"),
                ("c_subset", "test_every_logged_value_is_allowed",
                 "LOG holds another value at its start"),
                # a generator's yields, a local's value
                ("c_genexpr", "test_every_logged_value_is_positive",
                 "TestB.test_every_logged_value_is_positive.<locals>.<genexpr> (nested at +1)'s "
                 "local x differs"),
                # the decimal context, read through Decimal
                ("c_decimal", "test_a_third_starts_with_threes",
                 "the decimal context holds another value at its start; "
                 "TestB.test_a_third_starts_with_threes's local s differs"),
                # a child's output
                ("c_child", "test_the_child_runs", "child process #1 (`<PY> -c import pathlib; "),
                # a product line
                ("c_product", "test_the_report_is_made",
                 "1 product line(s) BASE reached are not reached after: src/zzprod/__init__.py:7")):
            with self.subTest(name):
                self.assertIn(b.format(name, test), said)
                self.assertIn(why, said[said.index(b.format(name, test)):].splitlines()[0])
        self.assertIn("outcomes: tests/test_fix_zz_n_skip.py: 1 test(s) do not pass at BASE, so "
                      "nothing shows what they do or check: TestA.test_a (skip: needs ZZ_RUN_A) "
                      "(compared: a pass, an expected failure, an unconditional `@unittest.skip` "
                      "with a constant reason): fix or prep the module so they pass here, or move "
                      "it whole", problems)

    def test_the_movers_new_refusals_off_the_verifier_fails_them(self):
        off = ((MT.SplitPlan, "_shared_state", lambda *a: None),
               (MT, "global_names", lambda s: set()))
        names = ("n_global", "n_global2", "n_global3", "n_tmp", "n_file")
        refused, problems, _notes = self.run_cases({n: REVIEW3[n] for n in names}, patches=off)
        self.assertEqual(refused, {})
        for name in names:
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        # n_global, its name copied only where it is read: TestA's NameError.
        self.assertIn("outcomes: tests/test_fix_zz_n_global.py: 1 test(s) differ after the split: "
                      "features.misc.test_zz_n_global_a.TestA.test_a pass -> error: NameError",
                      problems)

    def test_clean_splits_pass(self):
        # The temp folders' names are seeded, so nothing of k_temp differs
        # between the BASE runs.
        clean = {n: s for n, s in CLEAN.items() if n != "k_thread"}
        # k_unloaded's product module is not loaded at TestB's start after
        # the split: its value is not compared, and counted.
        self.assertEqual(self.run_cases(clean, CLEAN_EXTRA), ({}, [], [
            "tests/test_fix_zz_k_unloaded.py: 1 value(s) BASE read not compared, their module not "
            "loaded at the part's start after the split: zzlimits 1"]))

    def test_a_nested_class_moved_in_its_function_differs(self):
        # TJ-4214: Python 3.13 puts a class body's first line in its
        # namespace, __firstlineno__; the recorder counts it from the body's
        # own first line (k_ids passes on 3.13), and where the class sits in
        # its function is in its key, so one moved within it differs. A
        # body that sets the name itself keeps its value (k_first passes).
        def moved(r):
            p = r / "tests" / "rules" / "ca" / "sl" / "test_zz_k_ids_b.py"
            cls = "        @dataclasses.dataclass\n        class Parse:\n            meta: dict\n\n"
            text = p.read_text()
            self.assertEqual(text.count(cls), 1)
            p.write_text(text.replace(cls, "\n" + cls[:-1]))
        first = '''"""A class body that sets its first line."""
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(True)


class TestB(unittest.TestCase):
    def test_b(self):
        class Row:
            __firstlineno__ = 2
            n = 1

        self.assertEqual(Row.n, 1)
'''
        refused, problems, _notes = self.run_cases({"k_ids": CLEAN["k_ids"], "k_first": first},
                                                   edit=moved)
        self.assertEqual((refused, len(problems)), ({}, 1), problems)
        parse = "TestB.test_rows_keep_their_lines.<locals>.Parse"
        self.assertTrue(problems[0].startswith(
            "outcomes: tests/test_fix_zz_k_ids.py: rules.ca.sl.test_zz_k_ids_b.TestB."
            "test_rows_keep_their_lines does or checks otherwise after the split: "
            f"{parse} (nested at +1) line +0 ran 1 time(s) at BASE, 0 after; "), problems)
        self.assertIn(f"; {parse} (nested at +2) line +0 ran 0 time(s) at BASE, 1 after; ",
                      problems[0])

    def test_a_clock_value_is_dropped_not_a_difference(self):
        # Two BASE runs end before HEAD's start and the third starts after
        # they end: a stamp the clock moves is dropped (or agrees), never a
        # difference HEAD alone shows.
        refused, problems, _notes = self.run_cases({"k_clock": '''"""A stamp."""
import time
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(True)


class TestB(unittest.TestCase):
    def test_b(self):
        stamp = round(time.time(), 1)
        self.assertGreater(stamp, 0)
'''})
        self.assertEqual(refused, {})
        self.assertEqual([p for p in problems if "does or checks otherwise" in p], [], problems)

    def test_a_part_unstable_at_base_refuses_until_accepted(self):
        # k_turn: a poll whose count each run's turn sets (a numbered file
        # each run makes: BASE's first two runs 0 and 1, HEAD's 2 and 3, BASE's
        # third 4), so the BASE runs disagree on its lines' counts and HEAD's
        # are none of theirs: refused, each line named with its key and its
        # counts; its locals and its assertion's argument (numbers) are
        # compared with the digit runs masked, and listed.
        turns = tempfile.TemporaryDirectory()
        self.addCleanup(turns.cleanup)
        case = {"k_turn": f'''"""A poll whose count the run's turn sets."""
import os
import unittest

_TURNS = {turns.name!r}


def _turn():
    i = 0
    while True:
        try:
            os.close(os.open(os.path.join(_TURNS, f"turn{{i}}"), os.O_CREAT | os.O_EXCL))
            return i
        except FileExistsError:
            i += 1


class TestA(unittest.TestCase):
    def test_a_polls(self):
        n = 0
        for _ in range(2 + _turn()):
            n += 1
        self.assertGreater(n, 1)


class TestB(unittest.TestCase):
    def test_b(self):
        self.assertEqual(sum([1, 1]), 2)
'''}
        refused, problems, notes = self.run_cases(case)
        label = "features.misc.test_zz_k_turn_a.TestA.test_a_polls"
        self.assertEqual((refused, len(problems)), ({}, 1), problems)
        self.assertTrue(problems[0].startswith(
            f"outcomes: tests/test_fix_zz_k_turn.py: {label}: "), problems)
        self.assertIn("differ between the BASE runs, so nothing shows them the same after the "
                      "split: ", problems[0])
        # (BASE's first two runs run side by side: their turns either way)
        self.assertRegex(problems[0], r"the count of TestA\.test_a_polls line \+2, at BASE "
                                      r"(3/4|4/3)/7 \(`line <own>\|TestA\.test_a_polls\|0\|0\|2`\)")
        keys = re.findall(r"\(`([^`]+)`\)", problems[0])
        self.assertTrue(all(k.startswith("line ") for k in keys), keys)
        self.assertEqual(len(notes), 1, notes)
        self.assertRegex(notes[0], re.escape(LEFT_OUT) + f"{re.escape(label)}: "
                                   r"`assertion #1 arg 1` [(](2/3|3/2)/6 -> \d+/\d+[)]")
        why = "a poll's count, the run's turn (a numbered temp file), test_fix_zz_k_turn.py:12"

        def accept(r):
            (r / "scripts").mkdir(exist_ok=True)
            (r / VT.UNSTABLE).write_text("test id\tpart key\twhy\n"
                                         + "".join(f"{label}\t{k}\t{why}\n" for k in keys))
        # Each accepted in HEAD's table: no refusal, each acceptance listed
        # (the turns go on: 5 to 9, the counts within each line's bounds).
        refused, problems, notes = self.run_cases(case, edit=accept)
        self.assertEqual((refused, problems), ({}, []))
        self.assertEqual(sorted(n for n in notes if n.startswith("accepted")), sorted(
            f"accepted as unstable (scripts/step9-unstable.tsv): {label}: `{k}`: {why}"
            for k in keys))


# The fifth round's cases (2026-11-17, split-review4 A/mods and mods2): TestA
# changes what TestB reads, through what no earlier round's record held
# (the environment, the decimal context, a class attribute, a product
# cache, a child's environment), or BASE's run does not pass; each split
# one class to a file (_ab3). Each must be refused by the mover or fail the
# verifier. x_coin draws from a wide range: the review's one bit is caught
# three times in four (two HEAD runs), as the docstring says.
_ENV_A = '''import os
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        os.environ["ZZ_SEEN"] = "1 2 3"
'''


REVIEW5 = {
    "x_basefail": '''"""A class fixture's copy of the environment."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = dict(os.environ)

    def test_every_seen_is_a_digit(self):
        self.assertEqual(dict(os.environ), self.env)
        for x in os.environ.get("ZZ_SEEN", "").split():
            self.assertTrue(x.isdigit())
''',
    "y_basefail": '''"""A class fixture's set of the environment's items."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = set(os.environ.items())

    def test_every_seen_is_a_digit(self):
        self.assertEqual(set(os.environ.items()) - self.env, set())
        for x in os.environ.get("ZZ_SEEN", "").split():
            self.assertTrue(x.isdigit())
''',
    "x_modskip": '''"""A module fixture that skips."""
''' + _ENV_A + '''

def setUpModule():
    try:
        import zz_no_such_extra  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("needs the zz extra")


class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        for x in os.environ.get("ZZ_SEEN", "").split():
            self.assertTrue(x.isdigit())
''',
    "x_clsskip": '''"""A class fixture that skips."""
import shutil
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("zz-no-such-tool"):
            raise unittest.SkipTest("needs zz-no-such-tool")

    def test_every_seen_is_a_digit(self):
        for x in os.environ.get("ZZ_SEEN", "").split():
            self.assertTrue(x.isdigit())
''',
    "x_decimal_clock": '''"""The decimal context, and a clock."""
import decimal
import time
import unittest
from decimal import Decimal


class TestA(unittest.TestCase):
    def test_a(self):
        decimal.getcontext().prec = 5


class TestB(unittest.TestCase):
    def test_a_third(self):
        t0 = time.time()
        s = str(Decimal(1) / Decimal(3))
        self.assertTrue(s.startswith("0.333"))
        self.assertLess(time.time() - t0, 60)
''',
    "x_decimal_inline": '''"""The decimal context, read through Decimal only."""
import decimal
import unittest
from decimal import Decimal


class TestA(unittest.TestCase):
    def test_a(self):
        decimal.getcontext().prec = 5


class TestB(unittest.TestCase):
    def test_a_third(self):
        self.assertTrue(str(Decimal(1) / Decimal(3)).startswith("0.333"))
''',
    "x_msg": '''"""An assertion's message holds the clock."""
import time
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_seen_has_a_digit(self):
        self.assertRegex(os.environ.get("ZZ_SEEN", "0"), "[0-9]", f"at {time.time()}")
''',
    "y_poll": '''"""A poll whose count the run's turn sets (zzturn; a deadline's was racy)."""
import zzturn
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_b(self):
        left = 2 + zzturn.take("../zz_y_poll_turn")
        while (left := left - 1) >= 0:
            self.assertLess(left, 60)
        self.assertRegex(os.environ.get("ZZ_SEEN", "0"), "[0-9]")
''',
    "y_childclock": '''"""A child that prints the clock, then what it inherited."""
import subprocess
import sys
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_the_child_reports(self):
        code = "import os, time; print('took', time.time()); print('seen', os.environ.get('ZZ_SEEN'))"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertIn("seen", r.stdout)
''',
    "y_childstable": '''"""A child that prints what it inherited."""
import subprocess
import sys
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_the_child_reports(self):
        code = "import os; print('seen', os.environ.get('ZZ_SEEN'))"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertIn("seen", r.stdout)
''',
    "x_child": '''"""A child that checks what it inherited, by its exit only."""
import subprocess
import sys
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_the_child_ends_well(self):
        code = ("import os, sys; sys.exit(0 if all(x.isdigit() for x in "
                "os.environ.get('ZZ_SEEN', '').split()) else 1)")
        self.assertEqual(subprocess.call([sys.executable, "-c", code]), 0)
''',
    "x_coin": '''"""Randomness an earlier class seeded."""
import random
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        random.seed(7)


class TestB(unittest.TestCase):
    def test_b(self):
        n = random.randrange(10 ** 9)
        self.assertLess(n, 10 ** 9)
''',
    "x_env_all": '''"""The environment, read in one expression."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, os.environ.get("ZZ_SEEN", "").split())))
''',
    "x_env_inline": '''"""The environment, read in an assertion."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_no_error_seen(self):
        self.assertFalse("error" in os.environ.get("ZZ_SEEN", ""))
''',
    "x_selfattr": '''"""A class attribute set on a base, read by a string."""
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        unittest.TestCase.zz_rows = ("1", "2")


class TestB(unittest.TestCase):
    def test_every_row_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, getattr(self, "zz_rows", ()))))
''',
    "c_seen2": '''"""A helper's list, read by a string."""
import unittest

from _support import zz_c_seen2


class TestA(unittest.TestCase):
    def test_a(self):
        zz_c_seen2.note("1")


class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, getattr(zz_c_seen2, "SEEN"))))
''',
    "x_prod_inline": '''"""A product function's cache."""
import unittest

import zzcache


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(zzcache.is_iso("2024-01-02"))


class TestB(unittest.TestCase):
    def test_the_cache_is_small(self):
        self.assertLessEqual(zzcache.is_iso.cache_info().currsize, 0)
''',
    "y_opaque": '''"""A StringIO's value."""
import io
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_no_error_is_written(self):
        buf = io.StringIO()
        buf.write(os.environ.get("ZZ_SEEN", ""))
        self.assertFalse("error" in buf.getvalue())
''',
    "x_clsbody": '''"""A class body run inside a test."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_b(self):
        class Probe:
            for x in os.environ.get("ZZ_SEEN", "").split():
                if not x.isdigit():
                    raise AssertionError(x)
        self.assertTrue(Probe)
''',
    "y_deep": '''"""A value nested past the digest's depth."""
''' + _ENV_A + '''

class TestB(unittest.TestCase):
    def test_b(self):
        tree = os.environ.get("ZZ_SEEN", "").split()
        for _ in range(70):
            tree = [tree]
        self.assertEqual(len(tree), 1)
''',
    "x_opaque": '''"""A StringIO two files would hold."""
import unittest
import io
_BUF = io.StringIO()


class TestA(unittest.TestCase):
    def test_a(self):
        print("1", file=_BUF)


class TestB(unittest.TestCase):
    def test_no_error_was_written(self):
        self.assertFalse("error" in _BUF.getvalue())
''',
}
REVIEW5_EXTRA = {
    "tests/_support/zz_c_seen2.py": '"""Review helper: a log."""\nSEEN = []\n\n\n'
                                    'def note(x):\n    SEEN.append(x)\n',
    "src/zzcache/__init__.py": '"""A product stand-in: a cached check."""\nimport functools\n\n\n'
                               '@functools.lru_cache(maxsize=None)\ndef is_iso(s):\n'
                               '    return len(s) == 10\n',
    # A run's turn in its tree: a numbered file each run makes (O_EXCL),
    # BASE's first two runs 0 and 1 either way, its third 2, HEAD's 0 and
    # 1; a product stand-in, so its locals and line counts are not parts.
    "src/zzturn/__init__.py": '"""A product stand-in: the turn of a run."""\nimport os\n\n\n'
                              'def take(prefix):\n    i = 0\n    while True:\n        try:\n'
                              '            os.close(os.open(f"{prefix}{i}", os.O_CREAT | os.O_EXCL'
                              ' | os.O_WRONLY))\n            return i\n'
                              '        except FileExistsError:\n            i += 1\n',
}


class TestTheFifthRoundsCases(_ReviewCase):
    def test_each_case_is_refused_or_fails(self):
        refused, problems, notes = self.run_cases(REVIEW5, REVIEW5_EXTRA)
        self.assertEqual(sorted(refused), ["x_opaque"])
        self.assertIn("tests/test_fix_zz_x_opaque.py: _BUF (line 4) goes into 2 new files",
                      refused["x_opaque"])
        for name in sorted(set(REVIEW5) - set(refused)):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        said = "\n".join(problems)
        otherwise = "does or checks otherwise after the split: "

        def b(name, test):
            return f"rules.ca.sl.test_zz_{name}_b.TestB.{test} {otherwise}"
        unset = "the environment's ZZ_SEEN is unset at its start"
        context = "the decimal context holds another value at its start"
        inherited = "runs with another ZZ_SEEN in its environment"
        dropped = "differ between the BASE runs, so nothing shows them the same after the split: "
        for name, part, why in (
                # P1-a: BASE's run does not pass, or a fixture skips
                ("x_modskip", "2 test(s) do not pass at BASE, so nothing shows what they do or "
                 "check: ", "TestA.test_a (not run), TestB.test_every_seen_is_a_digit (not run)"),
                ("x_modskip", "a fixture fails or skips at BASE: ",
                 "setUpModule (test_fix_zz_x_modskip): skip: needs the zz extra"),
                ("x_clsskip", "a fixture fails or skips at BASE: ",
                 "setUpClass (test_fix_zz_x_clsskip.TestB): skip: needs zz-no-such-tool"),
                # ... and with the seed out of os.environ, the snapshot cases pass
                # at BASE; the environment they read differs after
                ("x_basefail", f"setUpClass TestB (rules.ca.sl.test_zz_x_basefail_b) {otherwise}",
                 unset),
                ("y_basefail", f"setUpClass TestB (rules.ca.sl.test_zz_y_basefail_b) {otherwise}",
                 unset),
                # P1-b: per local, per argument (a message among them), per line
                ("x_decimal_clock", b("x_decimal_clock", "test_a_third"), context),
                ("x_msg", b("x_msg", "test_seen_has_a_digit"),
                 "assertion #1 (assertRegex at TestB.test_seen_has_a_digit line +1) has another "
                 "argument 1"),
                ("y_poll", b("y_poll", "test_b"), unset),
                ("y_childclock", b("y_childclock", "test_the_child_reports"),
                 "child process #1 (`<PY> -c import os, time; print('took', time.time()); "
                 "print('`, exit 0) " + inherited),
                ("y_childclock", b("y_childclock", "test_the_child_reports"),
                 "assertion #1's arg 2 differs beyond the digits the BASE runs disagree on"),
                # a poll's assertions: only their site dropped, the next compared
                ("y_poll", b("y_poll", "test_b"),
                 "assertion 1 of 1 at TestB.test_b line +4 (assertRegex) has another argument 1"),
                ("y_poll", "rules.ca.sl.test_zz_y_poll_b.TestB.test_b: 3 part(s) " + dropped,
                 "the assertions at TestB.test_b line +3, which and how many: at BASE "),
                # P1-d: three BASE runs, two HEAD runs
                ("x_coin", b("x_coin", "test_b"), "TestB.test_b's local n differs"),
                # P1-c: values tied to the names the code reads
                ("x_env_all", b("x_env_all", "test_every_seen_is_a_digit"), unset),
                ("x_env_inline", b("x_env_inline", "test_no_error_seen"), unset),
                ("x_decimal_inline", b("x_decimal_inline", "test_a_third"), context),
                ("x_selfattr", b("x_selfattr", "test_every_row_is_a_digit"),
                 "the class attribute zz_rows is unset at its start"),
                ("c_seen2", b("c_seen2", "test_every_seen_is_a_digit"),
                 "_support.zz_c_seen2.SEEN holds another value at its start"),
                ("x_prod_inline", b("x_prod_inline", "test_the_cache_is_small"),
                 "zzcache.is_iso.cache_info holds another value at its start"),
                ("y_opaque", b("y_opaque", "test_no_error_is_written"),
                 "TestB.test_no_error_is_written's local buf differs"),
                # P3: a child's environment; a class body run inside a test
                ("x_child", b("x_child", "test_the_child_ends_well"), inherited),
                ("y_childstable", b("y_childstable", "test_the_child_reports"), inherited),
                ("x_clsbody", b("x_clsbody", "test_b"),
                 "TestB.test_b.<locals>.Probe (nested at +1) line +1 ran 4 time(s) at BASE, 1 "
                 "after"),
                # a cut is a part dropped
                ("y_deep", "rules.ca.sl.test_zz_y_deep_b.TestB.test_b: 1 part(s) " + dropped,
                 "TestB.test_b's local tree cut at 60 levels (`cut local "
                 "<own>|TestB.test_b|0|0|r|tree`)")):
            with self.subTest(name, why=why[:40]):
                head = f"outcomes: tests/test_fix_zz_{name}.py: {part}"
                self.assertIn(head, said)
                self.assertIn(why, said[said.index(head):].splitlines()[0])
        # The clock's parts (a stamp, a message, a child's line that holds
        # one) are compared with the digit runs the BASE runs disagree on
        # masked, each listed: no longer a part dropped whole.
        listed = "\n".join(notes)
        for name, test, key in (
                ("x_decimal_clock", "test_a_third", "local <own>|TestB.test_a_third|0|0|r|t0"),
                ("x_msg", "test_seen_has_a_digit", "assertion #1 arg 3"),
                ("y_childclock", "test_the_child_reports", "child process #1 stdout line 1")):
            with self.subTest(name, key=key):
                self.assertIn(f"{LEFT_OUT}rules.ca.sl.test_zz_{name}_b.TestB.{test}: ", listed)
                self.assertIn(f"`{key}` (", listed)



# The sixth round's cases (2026-11-17, split-review5 mods5): a value BASE
# read that HEAD lacks (z_absent); a part the BASE runs disagree on only
# in part, accepted whole before (z_local, z_out: a timestamp beside what
# TestA changed; z_poll0: a poll that no longer runs); a skip a condition
# binds (z_condskip); a loop's count three against two (z_loop); a product
# module imported in a function (z_prodlazy: not compared, counted). Each
# must be refused or fail the verifier, but z_prodlazy, whose value is
# counted; z_tz (z_local without the timestamp) is the control.
_TZ_A = '''import os
import time
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
'''
_ARGV_A = '''import sys
import time
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        sys.argv.append("--zz")
'''
REVIEW6 = {
    "z_absent": '''"""A helper's log, made at its first note."""
import unittest

from _support import zz_z_absent


class TestA(unittest.TestCase):
    def test_a(self):
        zz_z_absent.note("1")


class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, getattr(zz_z_absent, "SEEN", ()))))
''',
    "z_local": '''"""A report's hour beside its timestamp."""
''' + _TZ_A + '''

class TestB(unittest.TestCase):
    def test_the_report_names_the_hour(self):
        doc = {"at": time.time(), "hour": time.localtime(0).tm_hour}
        self.assertIn("hour", doc)
''',
    "z_out": '''"""A report's hour beside its timestamp, printed."""
import contextlib
import io
''' + _TZ_A + '''

class TestB(unittest.TestCase):
    def test_the_report_names_the_hour(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            print("at", time.time())
            print("hour", time.localtime(0).tm_hour)
        self.assertIn("hour", buf.getvalue())
''',
    "z_poll0": '''"""A poll that runs only while TestA's argument is there (its count the
run's turn sets, zzturn; a deadline's was racy)."""
import zzturn
''' + _ARGV_A + '''

class TestB(unittest.TestCase):
    def test_b(self):
        left = (1000 + zzturn.take("../zz_z_poll0_turn")) * (len(sys.argv) > 2)
        while (left := left - 1) >= 0:
            pass
        self.assertTrue(True)
''',
    "z_condskip": '''"""A skip a condition binds."""
import os
import shutil
import unittest

skip = unittest.skip if not shutil.which("zz-no-such-tool") else (lambda why: lambda f: f)


class TestA(unittest.TestCase):
    def test_a(self):
        os.environ["ZZ_SEEN"] = "1 2 3"


class TestB(unittest.TestCase):
    @skip("needs zz-no-such-tool")
    def test_every_seen_is_a_digit(self):
        for x in os.environ.get("ZZ_SEEN", "").split():
            self.assertTrue(x.isdigit())
''',
    "z_loop": '''"""A loop over the arguments, one more at BASE."""
''' + _ARGV_A + '''

class TestB(unittest.TestCase):
    def test_each_argument_is_text(self):
        for a in reversed(sys.argv):
            pass
        self.assertTrue(time.time() > 0)
''',
    "z_prodlazy": '''"""A product cache, imported in a function after the split."""
import unittest

import zzcache


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertTrue(zzcache.is_iso("2024-01-02"))


class TestB(unittest.TestCase):
    def test_the_cache_is_small(self):
        import zzcache as c
        self.assertTrue(c.is_iso.cache_info().currsize <= 8)
''',
    "z_tz": '''"""A report's hour (z_local's control: no timestamp)."""
''' + _TZ_A + '''

class TestB(unittest.TestCase):
    def test_the_report_names_the_hour(self):
        doc = {"hour": time.localtime(0).tm_hour}
        self.assertIn("hour", doc)
''',
}
REVIEW6_EXTRA = {
    **REVIEW5_EXTRA,
    "tests/_support/zz_z_absent.py": '"""Review helper: a log made at its first note."""\n\n\n'
                                     'def note(x):\n    globals().setdefault("SEEN", []).append(x)\n',
}


def _rows6(why):
    """The review's acceptances of z_local, z_out and z_poll0, each WHY."""
    def edit(r):
        b = "rules.ca.sl.test_zz_{}_b.TestB.{}\t{}\t{}\n"
        hour = "test_the_report_names_the_hour"
        rows = [b.format("z_local", hour, "assertion #1 arg 2", why("z_local", 14)),
                b.format("z_local", hour, f"local <own>|TestB.{hour}|0|0|r|doc", why("z_local", 14)),
                b.format("z_out", hour, "assertion #1 arg 2", why("z_out", 17)),
                b.format("z_out", hour, f"local <own>|TestB.{hour}|0|0|r|buf", why("z_out", 17)),
                b.format("z_poll0", "test_b", "line <own>|TestB.test_b|0|0|2", why("z_poll0", 17)),
                b.format("z_poll0", "test_b", "line <own>|TestB.test_b|0|0|3", why("z_poll0", 17))]
        (r / "scripts").mkdir(exist_ok=True)
        (r / VT.UNSTABLE).write_text("test id\tpart key\twhy\n" + "".join(rows))
    return edit


class TestTheSixthRoundsCases(_ReviewCase):
    def test_each_case_is_refused_or_fails(self):
        # The review's rows, with reasons that name the source and the line.
        refused, problems, notes = self.run_cases(
            REVIEW6, REVIEW6_EXTRA,
            edit=_rows6(lambda n, at: f"a poll's count, the run's turn (a numbered temp file), "
                                      f"at test_fix_zz_{n}.py:{at}" if n == "z_poll0" else
                        f"the clock: time.time() at test_fix_zz_{n}.py:{at}"))
        self.assertEqual(refused, {})
        for name in sorted(set(REVIEW6) - {"z_prodlazy"}):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        said = "\n".join(problems)
        otherwise = "does or checks otherwise after the split: "

        def b(name, test):
            return f"rules.ca.sl.test_zz_{name}_b.TestB.{test} {otherwise}"
        hour = "test_the_report_names_the_hour"
        for name, part, why in (
                # F1: a helper's name BASE read, absent where the helper is loaded
                ("z_absent", b("z_absent", "test_every_seen_is_a_digit"),
                 "_support.zz_z_absent.SEEN is unset at its start"),
                # F2: compared with the timestamp's digits masked, the hour's kept
                ("z_local", b("z_local", hour), f"TestB.{hour}'s local doc differs beyond the "
                                                "digits the BASE runs disagree on"),
                ("z_local", b("z_local", hour), "assertion #1's arg 2 differs beyond the digits"),
                ("z_out", b("z_out", hour), f"TestB.{hour}'s local buf differs beyond the digits "
                                            "the BASE runs disagree on"),
                ("z_out", b("z_out", hour), "assertion #1's arg 2 differs beyond the digits"),
                # F2: an accepted poll's count stays nonzero and in range
                ("z_poll0", "rules.ca.sl.test_zz_z_poll0_b.TestB.test_b: `line <own>|TestB.test_b|"
                 "0|0|3`, accepted as unstable, breaks its bounds: ",
                 "TestB.test_b line +3 ran 0 time(s) after, "),
                ("z_poll0", "rules.ca.sl.test_zz_z_poll0_b.TestB.test_b: `line <own>|TestB.test_b|"
                 "0|0|2`, accepted as unstable, breaks its bounds: ",
                 "TestB.test_b line +2 ran 1 time(s) after, "),
                # F5: a skip a condition binds is no skip by hand
                ("z_condskip", "1 test(s) do not pass at BASE, so nothing shows what they do or "
                 "check: ", "TestB.test_every_seen_is_a_digit (skip: needs zz-no-such-tool)"),
                # F5: a loop's count three against two: labelled, rerun
                ("z_loop", b("z_loop", "test_each_argument_is_text"),
                 "(only a loop's count differs, by a few: rerun it; the same counts again are "
                 "real)"),
                ("z_tz", b("z_tz", hour), f"TestB.{hour}'s local doc differs")):
            with self.subTest(name, why=why[:40]):
                head = f"outcomes: tests/test_fix_zz_{name}.py: {part}"
                self.assertIn(head, said)
                self.assertIn(why, said[said.index(head):].splitlines()[0])
        listed = "\n".join(notes)
        # F4: a product module not loaded at the part's start: its value not
        # compared, counted; the rows nothing dropped: each to go.
        self.assertIn("tests/test_fix_zz_z_prodlazy.py: 1 value(s) BASE read not compared, their "
                      "module not loaded at the part's start after the split: zzcache 1", listed)
        self.assertIn(f"scripts/step9-unstable.tsv accepts rules.ca.sl.test_zz_z_local_b.TestB."
                      f"{hour} `local <own>|TestB.{hour}|0|0|r|doc`, which no BASE run dropped "
                      f"this time: the row goes", listed)

    def test_a_reason_names_the_source_and_its_line(self):
        # The review's rows as written ("holds a timestamp", "a poll"): refused.
        _refused, problems, _notes = self.run_cases(
            {"z_poll0": REVIEW6["z_poll0"]}, REVIEW6_EXTRA,
            edit=_rows6(lambda n, at: "holds a timestamp"))
        self.assertIn("outcomes: scripts/step9-unstable.tsv:6: the reason names no unstable source "
                      "(time, clock, mtime, stat, pid, thread, poll, random, temp, port, hash, "
                      "order, environment) and its line (`:NNN`)", problems)
        self.assertTrue(any("rules.ca.sl.test_zz_z_poll0_b.TestB.test_b: 2 part(s) differ between "
                            "the BASE runs" in p for p in problems), problems)


# The seventh round's cases (2026-11-17, split-review5 mods6): OLD's list
# read through sys.modules by a name (z_own2: the mover refuses it; made by
# hand, the verifier) or a helper (z_own10); a stamp an earlier class's
# time zone shifts, inside the digits the BASE runs disagree on (z_stamp);
# a leak only one BASE run shows, HEAD the odd run's (z_odd: each run takes
# a numbered file, the third sees TestA's flag); a constant of OLD's read
# through a helper (z_constget). Each must fail; z_const (a constant a new
# file lacks, named by a string only: r7e_stats' `US`) and z_clock6 (the
# clock seven times: every left-out run listed, in full) are controls.
_HELPER_OF = '''import unittest

from _support import zz_z_own10
'''
REVIEW7 = {
    "z_own2": '''"""OLD's list, read through sys.modules by a name."""
import sys
import unittest

SEEN = []


class TestA(unittest.TestCase):
    def test_a(self):
        SEEN.append("1")


class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        name = type(self).__module__
        self.assertTrue(all(map(str.isdigit, getattr(sys.modules[name], "SEEN", ()))))
''',
    "z_own10": '''"""OLD's list, read through a helper."""
''' + _HELPER_OF + '''
SEEN = []


class TestA(unittest.TestCase):
    def test_a(self):
        SEEN.append("1")


class TestB(unittest.TestCase):
    def test_every_seen_is_a_digit(self):
        self.assertTrue(all(map(str.isdigit, getattr(zz_z_own10.module_of(self), "SEEN", ()))))
''',
    "z_constget": '''"""OLD's constant, read through a helper."""
''' + _HELPER_OF + '''
DIGITS = ("1", "2")


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertEqual(len(DIGITS), 2)


class TestB(unittest.TestCase):
    def test_every_digit_is_one(self):
        self.assertTrue(all(map(str.isdigit, getattr(zz_z_own10.module_of(self), "DIGITS", ()))))
''',
    "z_stamp": '''"""A backup's stamp, in TestA's time zone (to the microsecond: the BASE
runs never agree on it, as on a second they may)."""
import datetime
''' + _TZ_A + '''

class TestB(unittest.TestCase):
    def test_the_backup_is_stamped(self):
        name = "taxjson.toml.bak-" + datetime.datetime.now().strftime("%H%M%S.%f")
        self.assertRegex(name, r"bak-\\d{6}\\.\\d{6}$")
''',
    "z_odd": '''"""A leak only the third BASE run shows (each run takes a numbered file)."""
import os
import sys
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        sys.argv.append("--zz")


class TestB(unittest.TestCase):
    def test_a_first_run_is_fresh(self):
        n = next(i for i in range(1, 9) if _take(i))
        stale = (1 if n >= 3 else 0) if len(sys.argv) > 2 else 1
        self.assertIn(stale, (0, 1))


def _take(i):
    try:
        os.close(os.open(f"../zz_odd_{i}", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True
''',
    "z_const": '''"""OLD's constant, the other class naming it by a string only."""
import unittest

US = ("usa", "US", "USD")


class TestA(unittest.TestCase):
    def test_a(self):
        self.assertEqual(US[1], "US")


class TestB(unittest.TestCase):
    def test_the_code_is_text(self):
        self.assertEqual(" ".join(("SYN", "US")), "SYN US")
''',
    "z_clock6": '''"""The clock, seven times."""
import time
import unittest


class TestA(unittest.TestCase):
    def test_a(self):
        pass


class TestB(unittest.TestCase):
    def test_the_clock_moves(self):
        t = (time.time(), time.time(), time.time(), time.time(), time.time())
        a, b, c, d, e = t
        self.assertTrue(t)
''',
}
REVIEW7_EXTRA = {
    **REVIEW5_EXTRA,
    "tests/_support/zz_z_own10.py": '"""Review helper: the module of a test."""\nimport sys\n\n\n'
                                    'def module_of(case):\n'
                                    '    return sys.modules[type(case).__module__]\n',
}


class TestTheSeventhRoundsCases(_ReviewCase):
    def test_each_case_fails(self):
        refused, problems, notes = self.run_cases(REVIEW7, REVIEW7_EXTRA)
        # G1: the mover refuses sys.modules read by a name.
        self.assertEqual(sorted(refused), ["z_own2"])
        self.assertIn("tests/test_fix_zz_z_own2.py: line 13 (TestB) reads its own module's names "
                      "by name (sys.modules[a name])", refused["z_own2"])
        said = "\n".join(problems)
        otherwise = "does or checks otherwise after the split: "
        for name in ("z_own10", "z_constget", "z_stamp", "z_odd"):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        for name in ("z_const", "z_clock6"):
            with self.subTest(name):
                self.assertFalse([p for p in problems if f"test_fix_zz_{name}.py" in p], problems)

        def b(name, test):
            return f"rules.ca.sl.test_zz_{name}_b.TestB.{test} {otherwise}"
        for name, part, why in (
                # G1: changed since OLD's import (and looked up on the module)
                ("z_own10", b("z_own10", "test_every_seen_is_a_digit"), "SEEN is unset at its "
                                                                         "start"),
                # a constant looked up on the module that lacks it
                ("z_constget", b("z_constget", "test_every_digit_is_one"), "DIGITS is unset at its "
                                                                            "start"),
                # G2: a run the BASE runs disagree on, outside their range
                # widened by its spread: dropped (a row names what it is)
                ("z_stamp", "rules.ca.sl.test_zz_z_stamp_b.TestB.test_the_backup_is_stamped: ",
                 "assertion #1's arg 1, its digit run 1: "),
                # G3: HEAD's value only the odd BASE run holds
                ("z_odd", "rules.ca.sl.test_zz_z_odd_b.TestB.test_a_first_run_is_fresh: ",
                 "after what only 1 of the 3 BASE runs hold (BASE run 3 / 3; ")):
            with self.subTest(name, why=why[:40]):
                head = f"outcomes: tests/test_fix_zz_{name}.py: {part}"
                self.assertIn(head, said)
                self.assertIn(why, said[said.index(head):].splitlines()[0])
        # G4: every left-out run listed, BASE's beside HEAD's, none cut.
        clock = [n for n in notes if "test_zz_z_clock6_b" in n]
        self.assertEqual(len(clock), 1, notes)
        self.assertTrue(clock[0].startswith(LEFT_OUT), clock)
        self.assertNotIn(" more", clock[0])
        keys = re.findall(r"`([^`]+)` \(", clock[0])
        self.assertEqual(len(keys), 7, clock)
        whole = clock[0][clock[0].index("`assertion #1 arg 1` ("):].split(")")[0]
        self.assertEqual(whole.count(" -> "), 5, whole)

    def test_the_mover_refuses_sys_modules_by_a_name(self):
        _refused, problems, _notes = self.run_cases(
            {"z_own2": REVIEW7["z_own2"]}, REVIEW7_EXTRA,
            patches=[(MT.SplitPlan, "_crossings", lambda *a: None)])
        # Made by hand: the verifier sees it (changed since OLD's import).
        self.assertTrue(any(
            "rules.ca.sl.test_zz_z_own2_b.TestB.test_every_seen_is_a_digit does or checks "
            "otherwise after the split: SEEN is unset at its start" in p for p in problems),
            problems)
        ES = MT._sibling("extract_support")
        for src, want in (("sys.modules[name]", ["sys.modules[a name]"]),
                          ("sys.modules.get(name)", ["sys.modules[a name]"]),
                          ("sys.modules[type(self).__module__]", ["sys.modules[__name__]"]),
                          ('sys.modules["json"]', []), ('sys.modules.get("json")', []),
                          ("sys.modules[name] = m", []), ("del sys.modules[name]", [])):
            with self.subTest(src):
                self.assertEqual(ES.own_namespace(ast.parse(src), "test_x"), want)


class TestTheSeventhRoundsComparison(unittest.TestCase):
    def test_a_name_of_olds_a_new_file_lacks(self):
        # Not compared where it holds what OLD's import left (a constant);
        # ABSENT where a BASE run's value changed since, where the import
        # did not make it, or where HEAD's code looked it up on the module.
        def rec(**kw):
            return _record(names=["SEEN"], loaded=["<own>"], **kw)
        same = rec(values={"<own>|SEEN": "s0"}, imported={"SEEN": "s0"})
        moved = rec(values={"<own>|SEEN": "s1"}, imported={"SEEN": "s0"})
        made = rec(values={"<own>|SEEN": "s1"}, imported={})
        head = rec(values={})
        unset = ["SEEN is unset at its start"]
        for why, bs, hs, want in (
                ("a constant", [same] * 3, [head] * 2, []),
                ("changed since the import", [moved] * 3, [head] * 2, unset),
                ("made after the import", [made] * 3, [head] * 2, unset),
                ("looked up", [same] * 3, [{**head, "missing": ["SEEN"]}, head], unset)):
            with self.subTest(why):
                self.assertEqual(VT.part_problems(bs, hs)[0], want)
        # Changed in one BASE run only: the runs disagree, the part is dropped.
        self.assertEqual(VT.part_problems([same, moved, same], [head] * 2)[1],
                         [("value <own>|SEEN", "the value of SEEN")])

    def test_a_digit_run_stays_within_bases_range(self):
        # Widened by BASE's own spread (2 at least; round 8, H1): outside
        # it, the part is dropped (a row names what the number is), never
        # a difference; the masked text or a run BASE agrees on differs.
        def f(*runs):
            return ["m", [str(x) for x in runs], None, "v"]
        bf = [f(10, 100), f(10, 104), f(10, 120)]
        for why, hf, want in (
                ("within", [f(10, 110), f(10, 121)], None),
                ("widened by its spread", [f(10, 140), f(10, 80)], None),
                ("above", [f(10, 141), f(10, 110)],
                 "its digit run 2: 141 after, outside BASE's 100 to 120 widened by its spread 20"),
                ("small: by 2", None, None)):
            if hf is None:
                self.assertIsNone(VT._run_fits(["0.1", "0.3"], "2.2"))
                self.assertIsNotNone(VT._run_fits(["0.1", "0.3"], "2.4"))
                continue
            with self.subTest(why):
                got = VT._runs_level(bf, hf)
                self.assertEqual(got[:2], (None, want))
                self.assertEqual(got[2], [f"100/104/120 -> {hf[0][1][1]}/{hf[1][1][1]}"])
        self.assertEqual(VT._runs_level(bf, [f(11, 110), f(10, 110)])[:2],
                         ("differs beyond the digits the BASE runs disagree on", None))
        # Adjacent runs (a timestamp's minute and second): read together,
        # between BASE's; else each by itself.
        bf = [f(1, 32), f(1, 32), f(2, 53)]
        for hf, want in (([f(2, 17), f(2, 10)], None), ([f(3, 10), f(2, 17)], "its digit runs "
                          "1 to 2 (3/10 after, not between BASE's 1/32 and 2/53): 10 after, "
                          "outside BASE's 32 to 53 widened by its spread 21")):
            with self.subTest(hf=hf):
                self.assertEqual(VT._runs_level(bf, hf)[1], want)
        # A run that is not a number: its length and kinds of character.
        self.assertIsNone(VT._run_fits(["a1f", "b20"], "c3d"))
        self.assertIn("not the length and characters", VT._run_fits(["a1f", "b20"], "c3de"))
        # The spread is printed exactly, however large (a clock's
        # microseconds on a loaded machine; `:g` printed 3.5e+06).
        self.assertIn("widened by its spread 3500000",
                      VT._run_fits(["1763380000000000", "1763380003500000"], "1763383600000000"))


# The eighth round's cases (2026-11-17, split-review5 mods7): a clock an
# earlier class shifts by an hour, far outside BASE's spread (z_shift, in
# microseconds: the BASE runs never agree on it, as on a second they may); a
# timestamp's later field shifted past a clock's width (z_pair); a value
# most BASE runs hold, HEAD's (z_odd: a marker the first BASE run makes in
# the shared tree, the others see). Each fails without a row and passes
# with one that names the source and its line; z_probe
# (the lookup probe: no `__getattr__` in `dir()`, a missing name's error
# as Python's) is a control; `dir()` itself is the split's to change.
REVIEW8 = {
    "z_shift": '''"""A stamp an earlier class shifts by an hour."""
''' + _ARGV_A + '''

class TestB(unittest.TestCase):
    def test_the_stamp_is_recent(self):
        stamp = time.time_ns() // 1000 + (3_600_000_000 if len(sys.argv) > 2 else 0)
        self.assertGreater(stamp, 0)
''',
    "z_pair": '''"""A pair, its second field shifted past a clock's width."""
''' + _ARGV_A + '''

class TestB(unittest.TestCase):
    def test_the_pair_is_made(self):
        pair = (int(time.time()), time.time_ns() % 1000 + (5000 if len(sys.argv) > 2 else 0))
        self.assertEqual(len(pair), 2)
''',
    "z_odd": '''"""A marker the first BASE run makes, the others see (made O_EXCL: BASE's
first two runs, side by side, never both make it)."""
import os
''' + _ARGV_A + '''

class TestB(unittest.TestCase):
    def test_a_first_run_is_fresh(self):
        seen = not _take("../zz_odd_marker")
        stale = (1 if seen else 0) if len(sys.argv) > 2 else 1
        self.assertIn(stale, (0, 1))


def _take(path):
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True
''',
    "z_probe": '''"""A name the module lacks, and what dir() shows."""
''' + _HELPER_OF + '''

class TestA(unittest.TestCase):
    def test_a(self):
        pass


class TestB(unittest.TestCase):
    def test_a_missing_name_raises(self):
        m = zz_z_own10.module_of(self)
        self.assertFalse(hasattr(m, "NOPE"))
        with self.assertRaises(AttributeError) as cm:
            m.NOPE2
        self.assertIn("has no attribute 'NOPE2'", str(cm.exception))
        self.assertEqual(cm.exception.name, "NOPE2")
        self.assertFalse("__getattr__" in dir(m) or "__getattr__" in vars(m))
''',
}


def _rows8(dropped, why):
    """A row for each part DROPPED names (case -> its test ids and part
    keys), each WHY[case]."""
    def edit(r):
        rows = "".join(f"{test}\t{k}\t{why[n]}\n" for n, got in sorted(dropped.items())
                       for test, k in got)
        (r / "scripts").mkdir(exist_ok=True)
        (r / VT.UNSTABLE).write_text("test id\tpart key\twhy\n" + rows)
    return edit


class TestTheEighthRoundsCases(_ReviewCase):
    def test_each_case_fails_without_a_row(self):
        refused, problems, notes = self.run_cases(REVIEW8, REVIEW7_EXTRA)
        self.assertEqual(refused, {})
        said = "\n".join(problems)
        for name in ("z_shift", "z_pair", "z_odd"):
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f"outcomes: tests/test_fix_zz_{name}.py: ")
                                    for p in problems), (name, problems))
        # H4: the probe shows in neither dir() nor vars(), its error Python's.
        self.assertFalse([p for p in problems if "test_fix_zz_z_probe.py" in p], problems)
        unstable = ": settle them by a prep edit, or accept each in scripts/step9-unstable.tsv"
        stamp = "local <own>|TestB.test_the_stamp_is_recent|0|0|r|stamp"
        for name, test, why in (
                # H1: an hour off, outside BASE's spread (a few seconds)
                ("z_shift", "test_the_stamp_is_recent",
                 r"TestB\.test_the_stamp_is_recent's local stamp, its digit run 1: \d+ after, "
                 r"outside BASE's \d+ to \d+ widened by its spread \d+ \(`" + re.escape(stamp)),
                # H2: the later field past a clock's width: each run by itself
                # (with the second before it where the BASE runs disagree on that)
                ("z_pair", "test_the_pair_is_made",
                 r"TestB\.test_the_pair_is_made's local pair, its digit (run \d+|runs \d+ to \d+ "
                 r"\(\d+/\d+ after, not between BASE's \d+/\d+ and \d+/\d+\)): \d+ after, "
                 r"outside BASE's \d+ to \d+ widened by its spread \d+"),
                # H3: HEAD's value two of the three BASE runs hold (the first
                # two BASE runs side by side: either makes the marker)
                ("z_odd", "test_a_first_run_is_fresh",
                 r"TestB\.test_a_first_run_is_fresh's local stale, after what 2, not all, of the "
                 r"3 BASE runs hold \(BASE run [12]\+3 / [12]\+3; at BASE [01] / [01] / 1, "
                 r"after 1 / 1\)")):
            with self.subTest(name, why=why[:40]):
                head = (f"outcomes: tests/test_fix_zz_{name}.py: rules.ca.sl.test_zz_{name}_b."
                        f"TestB.{test}: ")
                self.assertIn(head, said)
                line = said[said.index(head):].splitlines()[0]
                self.assertIn(unstable, line)
                self.assertRegex(line, why)
        self.assertFalse([n for n in notes if "matches one BASE run" in n], notes)

        # With a row for each part dropped, naming what it is and its line
        # (rule 9): no refusal, each acceptance listed.
        dropped: dict = {}
        for p in problems:
            m = re.match(r"outcomes: tests/test_fix_zz_(\w+)\.py: (\S+): ", p)
            if m and unstable in p:
                dropped.setdefault(m[1], []).extend(
                    (m[2], k) for k in re.findall(r"\(`([^`]+)`\)", p))
        self.assertEqual(sorted(dropped), ["z_odd", "z_pair", "z_shift"])
        whys = {"z_shift": "the clock: a stamp, time.time_ns() in microseconds, at "
                           "test_fix_zz_z_shift.py:14",
                "z_pair": "the clock: the pair's fields, time.time() and time.time_ns(), at "
                          "test_fix_zz_z_pair.py:14",
                "z_odd": "a temp marker the first BASE run makes in the tree the BASE runs "
                         "share, the others see, at test_fix_zz_z_odd.py:16"}
        refused, problems, notes = self.run_cases({n: REVIEW8[n] for n in dropped}, REVIEW7_EXTRA,
                                                  edit=_rows8(dropped, whys))
        self.assertEqual((refused, problems), ({}, []))
        accepted = [n for n in notes if n.startswith("accepted as unstable")]
        self.assertEqual(len(accepted), sum(map(len, dropped.values())), notes)

    def test_the_real_wraps_still_pass(self):
        # A timestamp's minute and second (test_fix_a2_locks, test_audit_2026_08_fixes:
        # its second wraps as its minute turns), and a pid beside a clock's epoch (each
        # run by itself, the later one past two integer digits): left out, listed.
        def f(*runs):
            return ["m", [str(x) for x in runs], None, "v"]
        for bf, hf in (
                ([f("01", "15.776194"), f("01", "15.717857"), f("02", "40.966789")],
                 [f("01", "57.157788"), f("01", "57.337335")]),
                ([f("01", "01"), f("01", "01"), f("02", "29")], [f("01", "53"), f("01", "53")]),
                ([f(29136896, 1794913263), f(24549414, 1794913262), f(22845642, 1794913349)],
                 [f(28581233, 1794913314), f(28861107, 1794913314)])):
            with self.subTest(bf=bf):
                got = VT._runs_level(bf, hf)
                self.assertEqual(got[:2], (None, None))
                self.assertEqual(len(got[2]), 2)
        # A later field of another width, or of three integer digits: each run by itself.
        self.assertFalse(VT._between([["1", "32"], ["2", "53"]], [["2", "5358"]]))
        self.assertFalse(VT._between([["1", "320"], ["2", "530"]], [["2", "100"]]))
        self.assertFalse(VT._between([["1", "32.5"], ["2", "53.75"]], [["2", "10.5"]]))
        self.assertTrue(VT._between([["1", "32.25"], ["2", "53.75"]], [["2", "10.50"]]))
        # An item no HEAD run agrees on (its difference found already): nothing more.
        c = VT._Cmp()
        VT._compare(c, "k", "said", "told", ["a", "b", "a"], [None] * 3, [], [])
        self.assertEqual((c.found, c.dropped), ([], []))


class TestTheFourthRoundsMover(unittest.TestCase):
    def test_a_comment_on_the_docstrings_closing_line_is_refused(self):
        # The `Formerly:` line would move it away from its text (test_fix_def_rest's `# pii-ok`).
        for text in ('"""One."""  # a note\nx = 1\n', '"""Two\nlines."""  # a note\n'):
            with self.subTest(text), self.assertRaisesRegex(
                    ValueError, r"a comment on the docstring's closing line \(line \d\)"):
                MT.add_formerly(text, "test_fix_a")
        with self.assertRaisesRegex(ValueError, "TestX: a comment on its docstring's closing line"):
            MT.add_class_formerly('class TestX:\n    """One."""  # a note\n', "TestX",
                                  "Formerly: test_fix_a.TestX")
        # A comment on a line of its own, or a `#` inside the text, is no such comment.
        self.assertEqual(MT.add_formerly('"""One # two."""\n# a note\n', "test_fix_a"),
                         '"""One # two.\n\nFormerly: test_fix_a"""\n# a note\n')

    def test_branches_on_the_machine_are_listed(self):
        text = ('"""Branches."""\nimport os\nimport shutil\nimport unittest\n\ntry:\n'
                '    import zz_maybe  # noqa: F401\nexcept ImportError:\n    zz_maybe = None\n\n\n'
                'class TestA(unittest.TestCase):\n'
                '    @unittest.skipIf(os.name == "nt", "posix")\n    def test_a(self):\n'
                '        if shutil.which("zz-tool"):\n            pass\n\n\n'
                'class TestB(unittest.TestCase):\n    def test_b(self):\n'
                '        if os.environ.get("ZZ_X"):\n            pass\n')
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, {**SPLIT_EXTRA, "tests/test_fix_zz_branch.py": text})
            plan = MT.SplitPlan(r, "test_fix_zz_branch", [("TestA", ALPHA), ("TestB", BETA)])
        self.assertEqual(plan.env_branches(), [(6, "an import tried"), (13, "os.name"),
                                               (15, "shutil.which"), (21, "os.environ")])
        self.assertIn("  branches on the machine (the verifier's runs see one side): line 6 (an "
                      "import tried), line 13 (os.name), line 15 (shutil.which), line 21 "
                      "(os.environ)", plan.describe())

    def test_the_verifier_needs_python_3_12(self):
        # BASE's copy, the one that records, refuses an older Python.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            base = _synthetic(r)
            err = io.StringIO()
            with mock.patch.object(sys, "version_info", (3, 11, 9)), \
                    mock.patch.object(sys, "version", "3.11.9 (main)"), \
                    mock.patch.object(VT, "verify", side_effect=AssertionError("verify ran")), \
                    redirect_stderr(err):
                self.assertEqual(VT.main([base, base, f"--repo={r}", "--trees", "--base-copy"]), 2)
        self.assertEqual(err.getvalue(), "verify: needs Python 3.12 or later (sys.monitoring "
                                         "records what each test does), not 3.11.9: run it with "
                                         "the dev venv's python3\n")


# Round 10 (TJ-4210): a batch whose BASE holds an earlier batch's split
# (batch A splits test_fix_split; batch B, from A's tree, splits a second
# module, or moves test_fix_demo whole, touching nothing of A's split).
# Each of A's files names its module, so the reverse check took A's split
# for a module named whole twice and refused every later batch.
SPLIT2_EXTRA = {"tests/test_fix_split2.py": '"""Second split demo."""\nimport unittest\n\n\n'
                                            "class TestP(unittest.TestCase):\n"
                                            "    def test_p(self):\n        pass\n\n\n"
                                            "class TestQ(unittest.TestCase):\n"
                                            "    def test_q(self):\n        pass\n"}
SPLIT2_PLACE = [("test_fix_split2::TestP", "rules/ca/sl/test_split2_p.py"),
                ("test_fix_split2::TestQ", "features/misc/test_split2_q.py")]
FRAG_D = "tests/features/misc/test_split_d.py"
EARLIER = "text: test_fix_split, which an earlier batch split, is named otherwise than at BASE ("
LEAVES = "): a batch's `Formerly:` lines name its own moves, and leave an earlier one's as they are"


class TestABatchAfterASplit(_SplitCase):
    def two(self, second, edit=None):
        """Batch A (SPLIT_PLACE), landed as BASE; batch B (SECOND, the
        mover's), then EDIT: (repo, base tree, head tree, base export,
        head export)."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        r, base, head = (Path(td.name) / n for n in ("repo", "base", "head"))
        for d in (r, base, head):
            d.mkdir()
        _synthetic(r, {**SPLIT_EXTRA, **SPLIT2_EXTRA})
        with _ruff(), redirect_stdout(io.StringIO()):
            MT.move(r, list(SPLIT_PLACE))
        _git(r, "add", "-A")
        tree = _git(r, "write-tree").strip()
        with _ruff(), redirect_stdout(io.StringIO()):
            MT.move(r, list(second))
        if edit:
            edit(r)
        _git(r, "add", "-A")
        head_tree = _git(r, "write-tree").strip()
        VT.export(tree, base, r)
        VT.export(head_tree, head, r)
        return r, tree, head_tree, base, head

    def test_a_later_batch_passes(self):
        for why, second, mods in (("a second split", SPLIT2_PLACE, {}),
                                  ("a whole move", MOVE, {"test_fix_demo": "rules.ca.sl.test_demo"})):
            with self.subTest(why):
                got = self.two(second)
                self.assertEqual(VT.batch_moves(got[3], got[4])[0], mods)
                # Only this batch's split: A's is BASE's, its module gone.
                self.assertEqual(sorted(VT.split_moves(got[3], got[4])),
                                 ["test_fix_split2"] if mods == {} else [])
                self.assertEqual(self.verify_text(*got), ([], []))

    def test_an_earlier_splits_files_stay_as_they_are(self):
        more = '"""More.\n\nFormerly: test_fix_split"""\nimport unittest\n\n\n' \
               "class TestD(unittest.TestCase):\n    def test_d(self):\n        pass\n"
        line = "Formerly: test_fix_split.TestC"

        def add(r):
            (r / FRAG_D).write_text(more)

        def drop(r):
            (r / FRAG_C).unlink()

        def rename(r):
            _git(r, "mv", FRAG_C, FRAG_D)

        def reline(r):
            p = r / FRAG_C
            p.write_text(p.read_text().replace(line, "Formerly: test_fix_split.TestZ"))
        c = FRAG_C[len("tests/"):]
        d = FRAG_D[len("tests/"):]
        a = FRAG_A[len("tests/"):]
        for why, edit, said, also in (
                ("a file added that claims it", add,
                 f"test_fix_split: {c}, {a} at BASE, {c}, {d}, {a} after",
                 f"text: {FRAG_D} is new, not a moved module or a package below tests/rules/ or "
                 f"tests/features/"),
                ("one of its files dropped", drop,
                 f"test_fix_split: {c}, {a} at BASE, {a} after; test_fix_split.TestC: {c} "
                 f"(TestC) at BASE, nowhere after", f"text: {FRAG_C} deleted, not moved"),
                ("one of its files moved", rename,
                 f"test_fix_split: {c}, {a} at BASE, {d}, {a} after; test_fix_split.TestC: {c} "
                 f"(TestC) at BASE, {d} (TestC) after", f"text: {FRAG_C} deleted, not moved"),
                ("a class line rewritten", reline,
                 f"test_fix_split.TestC: {c} (TestC) at BASE, nowhere after; "
                 f"test_fix_split.TestZ: nowhere at BASE, {c} (TestC) after", None)):
            for second in (MOVE, SPLIT2_PLACE):
                with self.subTest(why, second=second[0][0]):
                    problems, _listed = self.verify_text(*self.two(second, edit))
                    self.assertIn(EARLIER + said + LEAVES, problems)
                    if also:
                        self.assertIn(also, problems)
        # The mover moves a module at tests/ only: a placement naming a
        # split's file is refused, so a batch never moves one.
        with tempfile.TemporaryDirectory() as td:
            r = Path(td)
            _synthetic(r, SPLIT_EXTRA)
            with _ruff(), redirect_stdout(io.StringIO()):
                MT.move(r, list(SPLIT_PLACE))
            _git(r, "add", "-A")
            with self.assertRaises(SystemExit) as cm, _ruff(), redirect_stdout(io.StringIO()):
                MT.move(r, [(Path(FRAG_C).stem, d)])
            self.assertIn(f"move_tests: tests/{Path(FRAG_C).stem}.py does not exist",
                          str(cm.exception))

    def test_a_whole_move_named_twice_still_fails(self):
        # TJ-3751 and TJ-3768 for a batch after a split: A's split exempt,
        # a whole move's name still counted.
        def twice(r):
            p = r / "tests" / "rules" / "ca" / "sl" / "test_split2_p.py"
            text = p.read_text()
            self.assertTrue(text.startswith('"""Second split demo.\n\nFormerly: test_fix_split2"""'))
            p.write_text(text.replace("Formerly: test_fix_split2", "Formerly: test_fix_split2\n"
                                      "Formerly: test_fix_demo", 1))
        problems, _listed = self.verify_text(*self.two(MOVE + SPLIT2_PLACE, twice))
        self.assertIn("text: test_fix_demo is named whole by 2 `Formerly:` lines "
                      "(rules/ca/sl/test_demo.py, rules/ca/sl/test_split2_p.py): a module moves to "
                      "one place", problems)
        self.assertFalse([p for p in problems if p.startswith("text: test_fix_split is named")],
                         problems)


if __name__ == "__main__":
    unittest.main()

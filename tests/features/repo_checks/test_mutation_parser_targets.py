"""The mutation harness's parser targets (PLAN step 16): scripts/mutation_audit.py
`--parser ID` mutates one parser (lib/cash_readers/ too until step 16 commit 11
moved its last reader into Kraken's parser), a sample of its sites
stratified by function, against the sets a line trace of every test module gives,
set B cut per mutant to the modules that ran the mutated line, without the modules
the run's control sets aside; each run's counts go in scripts/mutation-runs.json.
Nothing is mutated here: the harness is imported (its runs are guarded) and its
sampler, its sets, its cut, its instrument and its control are run on made-up
sites, traces and files."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _paths
from taxjson.parsers import registry

REPO = _paths.REPO
RUNS = REPO / "scripts" / "mutation-runs.json"
RUN_KEYS = ("parser", "commit", "tree", "sites", "n", "seed", "sampled", "killed",
            "timeouts", "cut_misses", "survived", "equivalent", "set_aside",
            "set_aside_per_mutant", "flagged", "lost", "unflushed", "trace", "trace_jobs",
            "control_jobs", "triage")
# lib/cash_readers/ was recorded as a row of its own until step 16 commits 7
# to 11 moved the readers into the parsers: the parsers alone since.
RECORDED = {s.id for s in registry.SPECS}


def _harness():
    spec = importlib.util.spec_from_file_location(
        "_mut_audit_parsers", REPO / "scripts" / "mutation_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _shallow() -> bool:
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--is-shallow-repository"],
                       capture_output=True, text=True)
    return r.returncode != 0 or r.stdout.strip() != "false"


def _commit_resolves(commit: str):
    """Whether `commit` is a commit of this checkout; None when that cannot be
    told (no git, or a shallow clone: CI's suite job checks out one commit)."""
    if _shallow():
        return None
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet",
                           commit + "^{commit}"], capture_output=True).returncode == 0


_ROOT_TREES: list = []


def _root_trees() -> set:
    """The trees of the commits this checkout's refs reach (a shallow clone:
    the ones it holds, HEAD's among them)."""
    if not _ROOT_TREES:
        r = subprocess.run(["git", "-C", str(REPO), "log", "--all", "--format=%T"],
                           capture_output=True, text=True)
        _ROOT_TREES.append(set(r.stdout.split()) if r.returncode == 0 else set())
    return _ROOT_TREES[0]


def _tree_resolves(entry):
    """Whether a record's tree is its commit's when the store holds that
    commit, else a commit's whole tree, one the refs reach (a rebase-merge
    gives a landed commit a new id and keeps its tree: the tree is what is
    checked), never a folder's (review H2: the docs/ subtree passed); None
    when the store lacks it and cannot be told to (a shallow clone holds
    HEAD's trees alone): the caller skips, it never passes."""
    git = ["git", "-C", str(REPO)]
    kind = subprocess.run(git + ["cat-file", "-t", entry["tree"]], capture_output=True,
                          text=True)
    if kind.returncode != 0:
        return None if _shallow() else False
    if kind.stdout.strip() != "tree":
        return False
    of = subprocess.run(git + ["rev-parse", "--verify", "--quiet", entry["commit"] + "^{tree}"],
                        capture_output=True, text=True)
    if of.returncode == 0:
        return of.stdout.strip() == entry["tree"]
    if entry["tree"] in _root_trees():
        return True
    return None if _shallow() else False


def _run_problems(entry, resolves=_tree_resolves) -> list:
    """What is wrong with one mutation-runs.json record ([] for none)."""
    if not isinstance(entry, dict) or tuple(sorted(entry)) != tuple(sorted(RUN_KEYS)):
        return [f"keys {sorted(entry) if isinstance(entry, dict) else entry!r}, "
                f"not {sorted(RUN_KEYS)}"]
    out = []
    if entry["parser"] not in RECORDED:
        out.append(f"parser {entry['parser']!r} is no registry id")
    if not isinstance(entry["commit"], str) or not re.fullmatch(r"[0-9a-f]{7,40}",
                                                                 entry["commit"]):
        out.append(f"commit {entry['commit']!r} is no commit id")
    elif not isinstance(entry["tree"], str) or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?",
                                                                 entry["tree"]):
        out.append(f"tree {entry['tree']!r} is no tree id")
    elif resolves(entry) is False:
        out.append(f"tree {entry['tree']} is no tree of this repository, or not commit "
                   f"{entry['commit']}'s")
    for key in ("set_aside", "set_aside_per_mutant", "flagged"):
        if not isinstance(entry[key], list) or not all(isinstance(v, str) and v
                                                       for v in entry[key]):
            out.append(f"{key} {entry[key]!r} is no list of names")
    if not out and not all(re.fullmatch(r"src/\S+\.py:\d+", v) for v in entry["flagged"]):
        out.append(f"flagged {entry['flagged']!r}: each a survivor's path:line")
    if not isinstance(entry["trace"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["trace"]):
        out.append(f"trace {entry['trace']!r} is no sha256 of the saved trace")  # TJ-4430
    for key in ("trace_jobs", "control_jobs"):          # TJ-4373
        if not isinstance(entry[key], int) or isinstance(entry[key], bool) or entry[key] < 1:
            out.append(f"{key} {entry[key]!r}: the --jobs the run's trace or control ran at")
    names = ("sites", "n", "seed", "sampled", "killed", "timeouts", "cut_misses", "survived",
             "equivalent", "lost", "unflushed")
    counts = [entry[k] for k in names]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts):
        return out + [f"counts {counts} are not whole numbers"]
    (sites, n, _seed, sampled, killed, timeouts, cut_misses, survived, equivalent, lost,
     _unflushed) = counts
    if lost:
        out.append(f"{lost} process(es) of the trace could not tell their module: such a run "
                   f"is not recorded")
    if not 1 <= n <= 150:
        out.append(f"a sample of {n}: 1 to 150")
    if not 0 < sampled <= min(sites, n):
        out.append(f"{sampled} sampled of {sites} sites, a sample of {n}: more than 0, "
                   f"and no more than either")
    if isinstance(entry["flagged"], list) and len(entry["flagged"]) > survived + equivalent:
        out.append(f"{len(entry['flagged'])} flagged of {survived + equivalent} survivors")
    if killed + timeouts + cut_misses + survived + equivalent != sampled:
        out.append(f"{killed} killed, {timeouts} timed out, {cut_misses} killed outside "
                   f"their cut, {survived} survived and {equivalent} equivalent of "
                   f"{sampled} sampled")
    if not isinstance(entry["triage"], str) or not re.fullmatch(r"(TJ-\d+)?", entry["triage"]):
        out.append(f"triage {entry['triage']!r} is no TJ id")
    elif survived + equivalent + cut_misses and not entry["triage"]:
        out.append("survivors and cut misses (each a bug of the trace) need the triage's "
                   "TJ id")
    return out


class TestParserTargets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.m = _harness()

    def test_the_targets_are_the_registrys_parsers_and_the_cash_readers(self):
        targets = self.m.PARSER_TARGETS
        self.assertEqual(set(targets), RECORDED)
        for s in registry.SPECS:
            self.assertEqual(targets[s.id], ("src/" + s.module.replace(".", "/") + ".py",), s.id)
        # The cash readers were a target until step 16 commit 11 moved the last
        # into its parser: none is left, so none is a target. A broker's cash
        # events are built with lib/cash_lines' builders (conv, move, balance,
        # flow): every module that imports them is a target's, so a reader
        # added back fails here until it is one.
        builders = []
        for p in sorted((REPO / "src" / "taxjson").rglob("*.py")):
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if (isinstance(n, ast.ImportFrom) and n.module == "taxjson.lib"
                        and any(a.name == "cash_lines" for a in n.names)) or (
                        isinstance(n, ast.ImportFrom) and n.module == "taxjson.lib.cash_lines"
                        and {a.name for a in n.names} & {"conv", "move", "balance", "flow"}) or (
                        isinstance(n, ast.Import)
                        and any(a.name == "taxjson.lib.cash_lines" for a in n.names)):
                    builders.append(p.relative_to(REPO).as_posix())
                    break
        self.assertEqual(sorted(set(builders) - set(self.m.TRACED_FILES)), [],
                         "a cash reader is back: make it a target")
        self.assertEqual(len(builders), 5)
        self.assertNotIn("cash_readers", targets)
        # One trace records every target's files.
        self.assertEqual(set(self.m.TRACED_FILES), {f for fs in targets.values() for f in fs})
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "mutation_audit.py"),
                            "--list-targets"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for tid in targets:
            self.assertIn(f"parser {tid}: ", r.stdout)
        # The sites of a real target sit in its functions, each one's lines its node's.
        sites = self.m.parser_sites(targets["coinbase"])
        self.assertGreater(len(sites), 100)
        self.assertTrue(all(s.line <= s.end_line and s.kind != "unaryneg" for s in sites))
        self.assertIn("CoinbaseBrokerage._col", {s.func for s in sites})

    def _sites(self, sizes):
        return [self.m.Site("src/x.py", i, "cmp", 10 * k + i, 10 * k + i, f"f{k}")
                for k, n in enumerate(sizes) for i in range(n)]

    def test_the_sample_is_stratified_repeatable_and_whole_below_n(self):
        sample, sites = self.m.stratified_sample, self._sites((10, 5, 1, 4))
        self.assertEqual(sample(sites, 20, 3), list(range(20)))
        self.assertEqual(sample(sites, 150, 3), list(range(20)))
        picked = sample(sites, 8, 1)
        self.assertEqual(picked, sample(sites, 8, 1))
        self.assertEqual(picked, sorted(set(picked)))
        by_func = {}
        for i in picked:
            by_func[sites[i].func] = by_func.get(sites[i].func, 0) + 1
        # One each, the other four in proportion to the other sites (9, 4, 0, 3 of 16:
        # floors 2, 1, 0, 0, the one left to the largest remainder, f3's).
        self.assertEqual(by_func, {"f0": 3, "f1": 2, "f2": 1, "f3": 2})
        self.assertGreater(len({tuple(sample(sites, 8, s)) for s in range(10)}), 1)
        # Fewer sites than functions: one each from as many functions.
        two = sample(sites, 2, 5)
        self.assertEqual(len({sites[i].func for i in two}), 2)

    def test_with_more_functions_than_the_sample_the_draw_weighs_their_sites(self):
        # A 45-site function beside twenty 1-site ones, 5 drawn: it is drawn
        # nearly always, a 1-site one about one draw in five (not 5 in 21 for
        # each, as an unweighted draw gave).
        sites = self._sites((45,) + (1,) * 20)
        drawn = {"f0": 0, "f1": 0}
        for seed in range(200):
            picked = self.m.stratified_sample(sites, 5, seed)
            funcs = [sites[i].func for i in picked]
            self.assertEqual(len(funcs), len(set(funcs)), seed)      # one site each
            for f in drawn:
                drawn[f] += f in funcs
        self.assertGreater(drawn["f0"], 190, drawn)
        self.assertLess(drawn["f1"], 80, drawn)
        self.assertEqual(self.m.stratified_sample(sites, 5, 7), self.m.stratified_sample(sites, 5, 7))

    def test_every_test_module_is_a_candidate(self):
        mods = self.m.candidate_modules()
        on_disk = [p for p in (REPO / "tests").rglob("test_*.py")
                   if not any(part.startswith("_") for part in p.relative_to(REPO / "tests").parts[:-1])]
        self.assertEqual(len(mods), len(on_disk))
        # One that names no parser (review F2: it ran Coinbase's code in a child).
        self.assertIn("features.planning.test_demo_project_view_wording_and_usage_lines", mods)
        self.assertIn("test_golden_views", mods)

    def test_the_cut_keeps_every_module_that_ran_the_line(self):
        trace = {"m1": {"lines": {"src/x.py": [10, 11]}}, "m2": {"lines": {"src/x.py": [20]}},
                 "m3": {"lines": {"src/y.py": [11]}}, "m4": {"lines": {"src/x.py": [12]}},
                 "m5": {"lines": {}}}
        mods = list(trace)
        cut = self.m.cut_to_line
        self.assertEqual(cut(mods, trace, "src/x.py", 11, 12), ["m1", "m4"])
        self.assertEqual(cut(mods, trace, "src/x.py", 30, 30), [])
        for mod, rec in trace.items():
            for rel, lines in rec["lines"].items():
                for ln in lines:
                    self.assertIn(mod, cut(mods, trace, rel, ln, ln))
                    self.assertIn(mod, cut(mods, trace, rel, ln - 1, ln + 1))
        # A node outside every function body runs at import: every module that
        # ran a line of the file ran it (the instrument starts after it).
        body = {10, 11, 12, 20}
        self.assertEqual(cut(mods, trace, "src/x.py", 3, 3, body), ["m1", "m2", "m4"])
        self.assertEqual(cut(mods, trace, "src/x.py", 11, 12, body), ["m1", "m4"])

    def test_the_sets_come_from_the_trace(self):
        def rec(rc, seconds, lines):
            return {"rc": rc, "seconds": seconds, "lines": {"src/x.py": lines}}
        trace = {"quick": rec(0, 1.5, [1, 6]), "slow": rec(0, 40.0, [7]),
                 "test_golden_views": rec(0, 90.0, [5]), "imports_only": rec(0, 2.0, [1, 2]),
                 "broken": rec(1, 3.0, [6]), "hung": rec("timeout", 900.0, [6]),
                 "no_tests": rec(5, 0.1, [])}
        a, b, c, failed = self.m.parser_sets(trace, {"src/x.py": {5, 6, 7}})
        self.assertEqual((a, b, c), (["quick"], ["slow"], ["test_golden_views"]))
        self.assertEqual(failed, ["broken", "hung"])

    @unittest.skipUnless(os.path.isdir("/proc/self"), "a child's ancestors are read from /proc")
    def test_the_instrument_records_the_lines_a_child_runs(self):
        # The instrument is in the traced file itself: a child a helper gives a
        # PYTHONPATH and an environment of its own (no MUTATION_TRACE_OUT) is
        # traced too, through its parent's environment (review F2).
        with tempfile.TemporaryDirectory() as td:
            code, out = Path(td, "code"), Path(td, "out")
            for d in (code, out):
                d.mkdir()
            src = ("def f(x):\n    return x < 3\n\n\ndef g():\n    return 1\n\n\n"
                   "def never():\n    return 2\n")
            target = code / "mt_probe.py"
            target.write_text(self.m.instrument_source(src))
            self.assertEqual(target.read_text().splitlines()[:10], src.splitlines())
            (code / "mt_main.py").write_text(
                "import subprocess, sys\nimport mt_probe\nmt_probe.f(1)\n"
                "subprocess.run([sys.executable, '-c', 'import mt_probe; mt_probe.g()'],"
                f" check=True, env={{'PYTHONPATH': {str(code)!r}, 'PATH': '/usr/bin:/bin'}})\n")
            env = dict(os.environ, PYTHONPATH=str(code), MUTATION_TRACE_OUT=str(out))
            r = subprocess.run([sys.executable, str(code / "mt_main.py")], env=env, cwd=td,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            records = [json.loads(p.read_text()) for p in out.glob("*.json")]
            self.assertEqual(len(records), 2)                   # the child's own record
            ran = set().union(*(set(rec[str(target)]) for rec in records))
            self.assertTrue({2, 6} <= ran, ran)                 # f's body, and g's in the child
            self.assertNotIn(10, ran)                           # never()'s body
            self.assertLessEqual(max(ran), 10)                  # none of the instrument's own
            # Outside a trace the instrumented file records nothing.
            r = subprocess.run([sys.executable, str(code / "mt_main.py")],
                               env={"PYTHONPATH": str(code), "PATH": "/usr/bin:/bin"}, cwd=td,
                               capture_output=True, text=True)
            self.assertEqual((r.returncode, len(list(out.glob("*.json")))), (0, 2), r.stderr)
            self.assertEqual(self.m._records(str(out))[1], 0)  # both recorded their end

    @unittest.skipUnless(os.path.isdir("/proc/self"), "an environment is read from /proc")
    def test_a_process_that_cannot_tell_its_module_is_counted(self):
        # Review G3: a process that found no MUTATION_TRACE_OUT recorded nothing,
        # silently. One that cleared os.environ before the import still finds
        # its own first environment; a child with a cleared environment whose
        # parent has exited finds none, and records into the trace's lost
        # folder; one that ends by os._exit is counted as unflushed.
        with tempfile.TemporaryDirectory() as td:
            code, out, lost = Path(td, "code"), Path(td, "out"), Path(td, "lost")
            for d in (code, out, lost):
                d.mkdir()
            src = "def f(x):\n    return x < 3\n"
            (code / "mt_probe.py").write_text(self.m.instrument_source(src, str(lost)))
            head = f"import os, sys, time\nsys.path.insert(0, {str(code)!r})\n"
            (code / "cleared.py").write_text(head + "os.environ.clear()\nimport mt_probe\n"
                                             "mt_probe.f(1)\n")
            (code / "exits.py").write_text(head + "import mt_probe\nmt_probe.f(1)\n"
                                           "os._exit(0)\n")
            (code / "orphan.py").write_text(
                head + "parent = int(sys.argv[1])\nt0 = time.time()\n"
                "while os.getppid() == parent and time.time() - t0 < 20:\n"
                "    time.sleep(0.02)\nimport mt_probe\nmt_probe.f(1)\n")
            (code / "parent.py").write_text(
                head + "import subprocess\n"
                f"child = subprocess.Popen([sys.executable, '-I', {str(code / 'orphan.py')!r}, "
                "str(os.getpid())], env={}, start_new_session=True)\n"
                f"open({str(Path(td, 'child.pid'))!r}, 'w').write(str(child.pid))\n")
            env = dict(os.environ, MUTATION_TRACE_OUT=str(out))
            for script in ("cleared.py", "exits.py", "parent.py"):
                r = subprocess.run([sys.executable, "-I", str(code / script)], env=env,
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            pid = Path(td, "child.pid").read_text()
            for _ in range(1000):
                if (lost / (pid + ".done")).exists():
                    break
                time.sleep(0.02)
            lines, unflushed = self.m._records(str(out))
            self.assertEqual(lines, {os.path.relpath(code / "mt_probe.py", self.m.REPO): {2}})
            self.assertEqual(len(list(out.glob("*.json"))), 1)  # cleared.py's: exits.py's
            self.assertEqual(unflushed, 1)                       # ended by os._exit, counted
            self.assertEqual(sorted(p.name for p in lost.iterdir()),
                             [pid + ".done", pid + ".json", pid + ".started"])

    # A made-up target of three functions: f's site on line 2, g's on line 6,
    # h with none (line 10, a body line a B module can run outside every cut).
    FAKE = ("def f(x):\n    return x < 3\n\n\ndef g(x):\n    return x > 1\n\n\n"
            "def h():\n    pass\n")

    def _planted(self, lines, fails, aside=(), unflushed=(), lost=0, shard=(0, 1)):
        """parser_main on FAKE (a sample of 2: one site of f, one of g) with a
        trace whose modules ran `lines`, a set's run (`_run_modules`, its
        modules in order, stopping at the first failure) failing a module when
        `fails(module, mutant)` is true (mutant 'f', 'g' or None: unmutated),
        running out when it is "timeout", the control setting `aside` aside,
        the trace's `unflushed` modules and `lost` processes: (stdout, the
        report, the modules of each run, the record or None, the control's
        calls)."""
        with tempfile.TemporaryDirectory() as td:
            rel = "src/fake.py"
            Path(td, "src").mkdir()
            Path(td, rel).write_text(self.FAKE)
            trace = {mod: {"rc": 0, "seconds": 40.0 if mod.startswith("b_") else 1.0,
                           "lines": {rel: hit}, "unflushed": int(mod in unflushed)}
                     for mod, hit in lines.items()}
            runs, controls = [], []

            def run_modules(mods, limit, root=None):
                self.assertEqual(root, td)
                text = Path(td, rel).read_text()
                mutant = ("f" if "x < 3" not in text else "g" if "x > 1" not in text
                          else None)
                runs.append((tuple(mods), mutant))
                for m in mods:
                    got = fails(m, mutant)
                    if got == "crash":
                        raise self.m.CopyCrashed("signal 9")
                    if got:
                        return ("timeout" if got == "timeout" else "fail"), m
                return "pass", ""

            def control(rels, a, b, c, jobs=1, root=None):
                controls.append((list(a), list(b), list(c)))
                return ([m for m in aside if m in a + b + c],
                        dict.fromkeys(["A", "C", *b], 1.0))
            said = io.StringIO()
            with mock.patch.object(self.m, "REPO", td), \
                    mock.patch.object(self.m, "REPORT", str(Path(td, "report.txt"))), \
                    mock.patch.dict(self.m.PARSER_TARGETS, {"fake": (rel,)}), \
                    mock.patch.dict(self.m.TRACE_LOSSES, {"lost": lost,
                                                          "unflushed": len(unflushed)}), \
                    mock.patch.dict(self.m.TRACE_MADE, {"jobs": 2, "sha256": "e" * 64}), \
                    mock.patch.object(self.m, "record_commit",
                                      lambda _c=None: ("abc1234", "f" * 40, "")), \
                    mock.patch.object(self.m, "load_trace", lambda *a, **k: trace), \
                    mock.patch.object(self.m, "install_restore_handlers", lambda: None), \
                    mock.patch.object(self.m, "_check_imports_here", lambda root=None: None), \
                    mock.patch.object(self.m, "make_copies", lambda n: [td] * n), \
                    mock.patch.object(self.m, "_make_copy", lambda dest: dest), \
                    mock.patch.object(self.m, "control_run", control), \
                    mock.patch.object(self.m, "_run_modules", run_modules), \
                    contextlib.redirect_stdout(said):
                self.m.parser_main("fake", 2, 0, shard=shard)
            out = said.getvalue()
            self.assertEqual(Path(td, rel).read_text(), self.FAKE)
            record = None
            if "survivors to equivalent): " in out:
                record = json.loads(out.split("survivors to equivalent): ", 1)[1]
                                    .splitlines()[0])
            return out, Path(td, "report.txt").read_text(), runs, record, controls

    LINES = {"a_near": [2], "a_far": [6], "b_slow": [2], "b_far": [6], "b_idle": [10],
             "test_golden_views": [2, 6, 10]}

    def test_a_killer_the_cut_misses_is_found_and_counted(self):
        # TJ-4333: set A is cut per mutant as B is, so the trace decides the
        # work; a mutant that passes its cuts is confirmed against the rest of
        # A before it is called a survivor. A killer planted outside the cut
        # (its trace says it never ran the mutated line) kills f's mutant
        # there: a cut miss, not a kill or a survivor. With a cut miss in the
        # run, g's survivor runs against the rest of B too (review of 5b), the
        # B module no cut held first run alone unmutated, and b_slow, outside
        # g's cut, kills it. Each kill is confirmed (5d, review H1): the module
        # that failed fails again alone with the mutant, and passes alone
        # without; the report names it.
        out, report, runs, record, controls = self._planted(
            self.LINES, lambda m, mutant: (m, mutant) in {("a_far", "f"), ("b_slow", "g")})
        self.assertEqual(runs, [
            (("a_near",), "f"), (("b_slow",), "f"), (("test_golden_views",), "f"),
            (("a_far",), "f"), (("a_far",), "f"), (("a_far",), None),  # the kill, confirmed
            (("a_far",), "g"), (("b_far",), "g"), (("test_golden_views",), "g"),
            (("a_near",), "g"),                                     # g survives A, B, C
            (("b_idle", "b_slow"), "g"),                            # longest first, by name
            (("b_slow",), "g"), (("b_slow",), None)])
        self.assertEqual(controls[1:], [([], ["b_idle"], [])])
        self.assertIn("killed by A (outside its cut): a_far", out)
        self.assertIn("killed by B (outside its cut): b_slow", out)
        self.assertIn("1 cut miss(es): the 1 survivor(s) run against the rest of B too", report)
        self.assertEqual({k: record[k] for k in ("sampled", "killed", "cut_misses", "survived",
                                                  "tree", "set_aside", "flagged")},
                         {"sampled": 2, "killed": 0, "cut_misses": 2, "survived": 0,
                          "tree": "f" * 40, "set_aside": [], "flagged": []})
        self.assertIn("cut misses (killed outside the cut: the trace missed the killer): 2",
                      report)

    def test_a_kill_counts_only_once_its_set_passes_unmutated(self):
        # Review of 5b: the control ran B's modules alone and each set once; a
        # set that fails together, or a flaky module, killed a mutant it did not
        # catch. a_near fails whatever the file holds: no kill. Since 5e
        # (TJ-4370) it is set aside for each mutant, named, and the judgement
        # goes on: both survive, flagged (it was set aside for each), and the
        # run is recorded; it is set aside per mutant, not for the whole run
        # (TJ-4432).
        out, report, runs, record, _c = self._planted(
            self.LINES, lambda m, mutant: m == "a_near")
        self.assertEqual(runs[:3], [(("a_near",), "f"), (("a_near",), "f"),
                                    (("a_near",), None)])
        self.assertIn("f: survived (A: 1 of 2, B: 1 of 3); set aside for it (failed, "
                      "unconfirmed): a_near", report)
        self.assertIn("unconfirmed (a set ran out before it named a module: no record): 0",
                      report)
        self.assertEqual({k: record[k] for k in ("killed", "survived", "set_aside",
                                                  "set_aside_per_mutant", "flagged")},
                         {"killed": 0, "survived": 2, "set_aside": [],
                          "set_aside_per_mutant": ["a_near"],
                          "flagged": ["src/fake.py:2", "src/fake.py:6"]})
        self.assertIn("src/fake.py:2 [cmp] (flagged: a_near set aside for it)", report)
        self.assertIn("src/fake.py:6 [cmp] (flagged: a_near set aside for it)", report)

    def test_a_module_that_fails_every_other_run_kills_nothing(self):
        # Review H1: a module failing on alternate runs passed the control,
        # failed under the mutant and passed the one unmutated rerun: "killed
        # by B". The module that failed must fail again alone with the mutant
        # before the unmutated rerun counts; here it passes: unconfirmed.
        ran = []

        def alternating(m, mutant):
            if m != "a_near":
                return False
            ran.append(mutant)
            return len(ran) % 2 == 1                # fails on its odd runs
        out, report, runs, record, _c = self._planted(self.LINES, alternating)
        self.assertEqual(runs[:2], [(("a_near",), "f"), (("a_near",), "f")])
        self.assertNotIn(None, ran)                # never rerun unmutated: no kill to confirm
        self.assertIn("set aside for it (failed, unconfirmed): a_near", out)
        self.assertNotIn("killed", report.split("\nkilled: ")[0])
        self.assertEqual((record["killed"], record["survived"], record["set_aside_per_mutant"]),
                         (0, 2, ["a_near"]))

    def test_a_module_that_fails_once_is_set_aside_and_the_set_goes_on(self):
        # Recheck of 5d, J1 (TJ-4370): the first module of f's cut failed once
        # (a flaky or order-dependent module), and judge returned "unconfirmed"
        # at it: the module after it, which kills f's mutant, never ran, and the
        # whole run went unrecorded. The module is set aside for that mutant
        # and named; the rest of the set runs; the kill counts.
        failed = []

        def fails(m, mutant):
            if m == "a_near" and not failed:
                failed.append(mutant)
                return True                         # its first run only
            return (m, mutant) == ("a_two", "f")
        lines = dict(self.LINES, a_two=[2])
        out, report, runs, record, _c = self._planted(lines, fails)
        self.assertEqual(runs[:5], [(("a_near", "a_two"), "f"), (("a_near",), "f"),
                                    (("a_two",), "f"), (("a_two",), "f"), (("a_two",), None)])
        self.assertIn("f: killed by A: a_two (A: 2 of 3, B: 1 of 3); set aside for it "
                      "(failed, unconfirmed): a_near", report)
        self.assertIsNotNone(record, out)
        self.assertEqual({k: record[k] for k in ("killed", "survived", "set_aside_per_mutant",
                                                  "flagged")},
                         {"killed": 1, "survived": 1, "set_aside_per_mutant": ["a_near"],
                          "flagged": []})

    def test_a_module_set_aside_for_one_mutant_flags_only_that_one(self):
        # Recheck of 5e, K3 (TJ-4432): a module set aside for one mutant (it
        # failed once, under f) joined the run's set_aside, and flagged g's
        # survivor too, whose line it reaches, though it ran and passed for g.
        # It flags f alone, and the record keeps it apart from the modules set
        # aside for the whole run.
        failed = []

        def fails(m, mutant):
            if m == "a_both" and not failed:
                failed.append(mutant)
                return True                         # its first run only, under f
            return False
        out, report, runs, record, _c = self._planted(dict(self.LINES, a_both=[2, 6]), fails)
        self.assertEqual(runs[:2], [(("a_both", "a_near"), "f"), (("a_both",), "f")])
        self.assertIn((("a_both", "a_far"), "g"), runs)        # it ran for g, and passed
        self.assertEqual({k: record[k] for k in ("survived", "set_aside", "set_aside_per_mutant",
                                                  "flagged")},
                         {"survived": 2, "set_aside": [], "set_aside_per_mutant": ["a_both"],
                          "flagged": ["src/fake.py:2"]})
        self.assertIn("src/fake.py:6 [cmp]\n", report)                # g: not flagged

    def test_a_module_that_runs_out_unmutated_too_is_set_aside_and_said(self):
        # Review H4: a module that ran out under a mutant was a kill ("timeout")
        # without a rerun. It is rerun alone unmutated: passing there, the
        # mutant ran it out; running out there too, it is set aside for that
        # mutant, named in its line and the record, and its set goes on.
        out, report, runs, record, _c = self._planted(
            self.LINES, lambda m, mutant: "timeout" if m == "b_slow" else False)
        self.assertEqual(runs[:4], [(("a_near",), "f"), (("b_slow",), "f"),
                                    (("b_slow",), None), (("test_golden_views",), "f")])
        self.assertIn("survived (A: 1 of 2, B: 1 of 3); set aside for it (ran out unmutated "
                      "too): b_slow", report)
        self.assertEqual((record["set_aside"], record["set_aside_per_mutant"]), ([], ["b_slow"]))
        self.assertEqual(record["flagged"], ["src/fake.py:2"])     # b_slow ran f's line
        self.assertEqual((record["timeouts"], record["survived"]), (0, 2))
        out, report, runs, record, _c = self._planted(
            self.LINES, lambda m, mutant: "timeout" if (m, mutant) == ("b_slow", "f")
            else False)
        self.assertIn("timeout by B: b_slow", report)
        self.assertEqual(runs[2], (("b_slow",), None))             # passes unmutated
        self.assertEqual((record["timeouts"], record["set_aside"]), (1, []))

    def test_a_mutant_whose_copy_is_killed_each_time_is_not_counted(self):
        # 5d: a set's process killed from outside (the OOM killer, a kill) is
        # no verdict of the mutant's: its copy is made again and the mutant
        # run again, twice; then it is "crashed", counted apart, and the run
        # is not recorded.
        out, report, runs, record, _c = self._planted(
            self.LINES, lambda m, mutant: "crash" if mutant == "g" else False)
        self.assertEqual(runs.count((("a_far",), "g")), 3)
        self.assertEqual(out.count("the copy is made again, and the mutant run again"), 2)
        self.assertIn("g: crashed by signal 9, 3 times (A: 1 of 2", report)
        self.assertIn("crashed (the copy was killed from outside 3 times: not counted, no "
                      "record): 1", report)
        self.assertIsNone(record)
        self.assertIn("no record: 1 mutant(s) crashed", out)

    def test_a_trace_that_lost_lines_of_a_cut_module_is_not_recorded(self):
        # Review H3: the trace's lost and unflushed processes were printed, not
        # recorded, and did not block a record. The record carries both; a
        # process that could not tell its module, or a module of A or B with
        # one that ended before its last record, refuses the record.
        def never(_m, _mutant):
            return False
        _o, _r, _runs, record, _c = self._planted(self.LINES, never)
        self.assertEqual((record["lost"], record["unflushed"]), (0, 0))
        lines = dict(self.LINES, elsewhere=[])                # in no set
        _o, _r, _runs, record, _c = self._planted(lines, never, unflushed=("elsewhere",))
        self.assertEqual((record["lost"], record["unflushed"]), (0, 1))
        out, _r, _runs, record, _c = self._planted(self.LINES, never, unflushed=("b_idle",))
        self.assertIsNone(record)
        self.assertIn("no record: 1 module(s) of A or B ended a process before its last "
                      "record in the trace, the first b_idle", out)
        out, _r, _runs, record, _c = self._planted(self.LINES, never, lost=2)
        self.assertIsNone(record)
        self.assertIn("no record: the trace lost 2 process(es)", out)

    def test_a_survivor_a_module_left_out_reaches_is_flagged(self):
        # Review of 5b: a survivor whose line a set-aside module ran was never
        # run against that module. The report and the record flag it, and name
        # the modules set aside; no cut miss, so no B pass.
        out, report, runs, record, controls = self._planted(
            self.LINES, lambda m, mutant: False, aside=("b_far",))
        self.assertEqual(len(controls), 1)
        self.assertEqual(record["set_aside"], ["b_far"])
        self.assertEqual(record["flagged"], ["src/fake.py:6"])
        self.assertEqual(record["survived"], 2)
        self.assertIn("set aside (failed in the control): b_far", report)
        self.assertIn("src/fake.py:6 [", report)
        self.assertIn("(flagged: reached by b_far, left out of the run)", report)
        self.assertEqual(report.count("(flagged"), 1)

    # A made-up target of six sites, each its function's body (lines 2 to 22),
    # and the test modules of a tree that runs them: f and g are killed by
    # test_fg, h and k by test_hk (which, under h's mutant, kills its own
    # process the first time MARK is there: a copy killed mid-run), p by the
    # slow test_pq_slow; q's mutant survives.
    SIX = ("def f(x):\n    return x < 3\n\n\ndef g(x):\n    return x > 1\n\n\n"
           "def h(x, y):\n    return x + y\n\n\ndef k(x, y):\n    return x * y\n\n\n"
           "def p(a, b):\n    return a and b\n\n\ndef q(x):\n    return x - 1\n")
    SIX_TESTS = {
        "test_fg": ("    def test_f(self):\n        self.assertEqual((t.f(2), t.f(3)), (True, False))\n"
                    "\n    def test_g(self):\n        self.assertEqual((t.g(1), t.g(2)), (False, True))\n"),
        "test_hk": ("    def test_h(self):\n        if t.h(2, 3) != 5:\n            try:\n"
                    "                os.rename(MARK, MARK + '.taken')\n"
                    "            except OSError:\n                pass\n            else:\n"
                    "                os.kill(os.getpid(), signal.SIGKILL)\n"
                    "        self.assertEqual(t.h(2, 3), 5)\n\n"
                    "    def test_k(self):\n        self.assertEqual(t.k(2, 3), 6)\n"),
        "test_pq_slow": ("    def test_p(self):\n        self.assertIs(t.p(True, False), False)\n\n"
                         "    def test_q_runs(self):\n        t.q(5)\n"),
        "test_golden_views": "    def test_f_runs(self):\n        t.f(1)\n"}

    def test_mutants_in_parallel_copies_report_as_one_at_a_time(self):
        # 5d: --jobs N runs N mutants at once, each in its own copy of the tree,
        # with real test processes. The report and the record are the same for
        # any N; a copy killed mid-run is made again and its mutant run again,
        # never counted; each kill names the module that made it.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as marks:
            mark = str(Path(marks, "kill-once"))
            rel = "src/six_target.py"
            for d in ("src", "tests/_hermetic"):
                Path(td, d).mkdir(parents=True)
            Path(td, rel).write_text(self.SIX)
            Path(td, "tests/_hermetic/__init__.py").write_text("")
            for mod, body in self.SIX_TESTS.items():
                Path(td, "tests", mod + ".py").write_text(
                    f"import os\nimport signal\nimport unittest\n\nimport six_target as t\n\n"
                    f"MARK = {mark!r}\n\n\nclass T(unittest.TestCase):\n\n{body}")
            ran = {"test_fg": [2, 6], "test_hk": [10, 14], "test_pq_slow": [18, 22],
                   "test_golden_views": [2]}
            trace = {mod: {"rc": 0, "seconds": 40.0 if mod == "test_pq_slow" else 1.0,
                           "lines": {rel: lines}, "unflushed": 0}
                     for mod, lines in ran.items()}

            def run(jobs):
                said = io.StringIO()
                with mock.patch.object(self.m, "REPO", td), \
                        mock.patch.object(self.m, "PY", sys.executable), \
                        mock.patch.object(self.m, "REPORT", str(Path(td, "report.txt"))), \
                        mock.patch.dict(self.m.PARSER_TARGETS, {"six": (rel,)}), \
                        mock.patch.dict(self.m.TRACE_LOSSES, {"lost": 0, "unflushed": 0}), \
                        mock.patch.dict(self.m.TRACE_MADE, {"jobs": 2, "sha256": "e" * 64}), \
                        mock.patch.object(self.m, "record_commit",
                                          lambda _c=None: ("abc1234", "f" * 40, "")), \
                        mock.patch.object(self.m, "load_trace", lambda *a, **k: trace), \
                        mock.patch.object(self.m, "install_restore_handlers", lambda: None), \
                        mock.patch.object(self.m, "_check_imports_here",
                                          lambda root=None: None), \
                        contextlib.redirect_stdout(said):
                    self.m.parser_main("six", 150, 0, jobs=jobs)
                self.assertEqual(Path(td, rel).read_text(), self.SIX)
                out = said.getvalue()
                return (Path(td, "report.txt").read_text(),
                        out.split("survivors to equivalent): ", 1)[1].splitlines()[0], out)
            one, record_one, _out = run(1)
            Path(mark).write_text("")
            three, record_three, out = run(3)
            self.assertFalse(Path(mark).exists())                   # a copy was killed
            self.assertTrue(Path(mark + ".taken").exists())
            self.assertIn("the copy is made again, and the mutant run again", out)
            # The same but for the line that says the jobs (TJ-4373).
            made = "trace eeeeeeeeeeeeeeee made at --jobs 2, the control run at --jobs {}"
            self.assertIn(made.format(1), one)
            self.assertEqual(three, one.replace(made.format(1), made.format(3)))
            self.assertEqual(json.loads(record_three),
                             dict(json.loads(record_one), control_jobs=3))
            verdicts = re.findall(r"\] (\w+): (\w+(?: by [^(]+)?) \(", one)
            self.assertEqual(verdicts, [
                ("f", "killed by A: test_fg"), ("g", "killed by A: test_fg"),
                ("h", "killed by A: test_hk"), ("k", "killed by A: test_hk"),
                ("p", "killed by B: test_pq_slow"), ("q", "survived")])
            self.assertIn("crashed (the copy was killed from outside 3 times: not counted, "
                          "no record): 0", one)
            self.assertEqual(json.loads(record_one)["killed"], 5)
            # The tree itself is never mutated nor run; the copies are removed.
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()),
                             ["report.txt", "src", "tests"])
            self.assertEqual(self.m._COPY_BASES, [])

    def test_the_control_sets_aside_what_fails_unmutated(self):
        # Review F1: a slow module that fails unmutated (a helper broken since
        # the trace) killed every mutant it ran on. The control runs the sets
        # once with the target unparsed and unmutated and sets aside the
        # modules that fail; the file is restored after.
        with tempfile.TemporaryDirectory() as td:
            rel = "src/t.py"
            Path(td, "src").mkdir()
            original = "x = 1  # a comment\n\n\ndef f():\n    return x\n"
            Path(td, rel).write_text(original)
            seen = []

            def passes(mods, limit, root=None):
                seen.append((tuple(mods), Path(td, rel).read_text()))
                return not set(mods) & {"b_broken", "a_broken"}
            with mock.patch.object(self.m, "REPO", td), \
                    mock.patch.object(self.m, "_passes", passes):
                aside, secs = self.m.control_run([rel], ["a_ok", "a_broken"],
                                                 ["b_ok", "b_broken"], ["c_ok"], jobs=2)
                self.assertEqual(aside, ["a_broken", "b_broken"])
                self.assertEqual(set(secs), {"A", "C", "b_ok", "b_broken"})
                self.assertEqual(Path(td, rel).read_text(), original)
                self.assertTrue(all(text == "x = 1\n\ndef f():\n    return x" for _m, text in seen))
                # A set that fails whole though no module fails alone aborts the run.
                said = io.StringIO()
                with mock.patch.object(self.m, "_passes",
                                       lambda mods, limit, root=None: len(mods) == 1), \
                        contextlib.redirect_stdout(said), self.assertRaises(SystemExit):
                    self.m.control_run([rel], ["a1", "a2"], [], [])
                self.assertIn("CONTROL FAILS (set A", said.getvalue())
                self.assertEqual(Path(td, rel).read_text(), original)

    def test_a_record_names_a_commit_only_for_its_own_tree(self):
        # Review F1: the record named HEAD for a tree that was not HEAD's.
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True)
            with mock.patch.object(self.m, "REPO", td):
                sha, tree, why = self.m.record_commit()
                self.assertEqual((sha, tree), (None, None))
                self.assertIn("HEAD is no commit of this repository", why)
        sha, tree, why = self.m.record_commit("0" * 40)
        self.assertEqual((sha, why.split(" is ")[0]), (None, "0" * 40))
        if _commit_resolves("HEAD"):
            dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain",
                                    "--untracked-files=all", "--", "src", "tests", "scripts"],
                                   capture_output=True, text=True).stdout.strip()
            ignored = self.m._git_files("--others", "--ignored", "--exclude-standard",
                                        tops=("src", "tests", "scripts"))
            sha, tree, why = self.m.record_commit()
            self.assertEqual(sha is None, bool(dirty or ignored), why)
            if sha:
                self.assertEqual(tree, subprocess.run(
                    ["git", "-C", str(REPO), "rev-parse", "HEAD^{tree}"],
                    capture_output=True, text=True).stdout.strip())
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "mutation_audit.py"),
                            "--parser", "coinbase", "--yes", "--commit", "0" * 40],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("refusing to run: " + "0" * 40 + " is no commit", r.stdout)
        self.assertIn("--no-record", r.stdout)

    def test_a_file_git_ignores_is_part_of_the_tree(self):
        # Review G1: an ignored tests/taxjson.toml or src/.../tmpx/json.py (a
        # `tmp*/` ignore) changed neither the record's check nor the trace's
        # fingerprint; a test reads it all the same. The tools' caches apart.
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True)
            Path(td, ".gitignore").write_text("tmp*/\n__pycache__/\n.ruff_cache/\n")
            Path(td, "src", "pkg").mkdir(parents=True)
            Path(td, "src", "pkg", "a.py").write_text("A = 1\n")
            with mock.patch.object(self.m, "REPO", td):
                before = self.m._fingerprint()
                for cache in ("src/pkg/__pycache__/a.cpython-312.pyc", "src/.ruff_cache/x"):
                    Path(td, cache).parent.mkdir(parents=True, exist_ok=True)
                    Path(td, cache).write_text("cache")
                self.assertEqual(self.m._fingerprint(), before)
                Path(td, "src", "pkg", "tmpx").mkdir()
                Path(td, "src", "pkg", "tmpx", "json.py").write_text("planted = 1\n")
                self.assertNotEqual(self.m._fingerprint(), before)
                self.assertEqual(self.m._git_files("--others", "--ignored", "--exclude-standard"),
                                 ["src/pkg/tmpx/json.py"])
        # The record's check refuses it (git's answers made up: no commit here).
        answers = {("rev-parse", "HEAD^{commit}"): "1" * 40, ("rev-parse", "1" * 40 + "^{tree}"):
                   "2" * 40, ("diff",): "", ("ls-files", "--exclude-standard"): "",
                   ("ls-files", "--ignored"): "src/pkg/tmpx/json.py\0tests/.ruff_cache/x\0"}

        def git(argv, **kw):
            for key, said in answers.items():
                if all(k in argv for k in key):
                    if key == ("ls-files", "--exclude-standard") and "--ignored" in argv:
                        continue
                    data = said.encode() if not kw.get("text") else said
                    return subprocess.CompletedProcess(argv, 0, data, b"" if not kw.get("text")
                                                       else "")
            raise AssertionError(argv)
        with mock.patch.object(self.m.subprocess, "run", git):
            sha, tree, why = self.m.record_commit()
            self.assertEqual((sha, tree), (None, None))
            self.assertIn("1 file(s) git ignores below src/, tests/ or scripts/", why)
            self.assertIn("the first src/pkg/tmpx/json.py", why)
            answers[("ls-files", "--ignored")] = "tests/.ruff_cache/x\0"
            self.assertEqual(self.m.record_commit(), ("1" * 10, "2" * 40, ""))

    def test_a_saved_trace_missing_a_candidate_is_traced_again(self):
        # Review G4: a saved trace with the tree's fingerprint but fewer modules
        # than the candidates was used: the modules it lacks were in no set.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "trace.json")
            fp = "9" * 64
            path.write_text(json.dumps({"fingerprint": fp, "lost": 0, "jobs": 1,
                                        "modules": {"m1": {"rc": 0, "seconds": 1, "lines": {}}}}))
            traced = []

            def trace_target(rels, modules, jobs=1):
                traced.append(list(modules))
                return {m: {"rc": 0, "seconds": 1, "lines": {}, "unflushed": 0}
                        for m in modules}, 3
            said = io.StringIO()
            with mock.patch.object(self.m, "_fingerprint", lambda: fp), \
                    mock.patch.object(self.m, "trace_target", trace_target), \
                    contextlib.redirect_stdout(said):
                with mock.patch.object(self.m, "candidate_modules", lambda: ["m1"]):
                    self.assertEqual(set(self.m.load_trace(path=str(path))), {"m1"})
                    self.assertEqual(traced, [])
                with mock.patch.object(self.m, "candidate_modules", lambda: ["m1", "m2"]):
                    self.assertEqual(set(self.m.load_trace(path=str(path))), {"m1", "m2"})
            self.assertEqual(traced, [["m1", "m2"]])
            self.assertIn("the saved trace holds 1 of the 2 test modules", said.getvalue())
            self.assertEqual(self.m.TRACE_LOSSES["lost"], 3)
            self.assertEqual(json.loads(path.read_text())["lost"], 3)

    def test_the_record_says_the_jobs_of_its_trace_and_control(self):
        # Recheck of 5d, J5 (TJ-4373): the trace's seconds (the A/B split, the
        # order a set runs in) and the control's (B's limits) are the
        # machine's under their jobs, so the report is the same for any --jobs
        # only for one saved trace. The saved trace says its jobs; one that
        # does not is traced again; the report and the record say both.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td, "trace.json")
            fp = "9" * 64
            path.write_text(json.dumps({"fingerprint": fp, "lost": 0, "modules": {
                "m1": {"rc": 0, "seconds": 1, "lines": {}, "unflushed": 0}}}))
            traced = []

            def trace_target(rels, modules, jobs=1):
                traced.append(jobs)
                return {m: {"rc": 0, "seconds": 1, "lines": {}, "unflushed": 0}
                        for m in modules}, 0
            said = io.StringIO()
            with mock.patch.object(self.m, "_fingerprint", lambda: fp), \
                    mock.patch.object(self.m, "trace_target", trace_target), \
                    mock.patch.object(self.m, "candidate_modules", lambda: ["m1"]), \
                    mock.patch.dict(self.m.TRACE_MADE, {"jobs": 0, "sha256": ""}), \
                    contextlib.redirect_stdout(said):
                self.m.load_trace(3, path=str(path))
                self.assertEqual((traced, json.loads(path.read_text())["jobs"]), ([3], 3))
                made = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(dict(self.m.TRACE_MADE), {"jobs": 3, "sha256": made})
                self.m.TRACE_MADE.update(jobs=0, sha256="")
                self.m.load_trace(1, path=str(path))          # reused: made at 3
                self.assertEqual((traced, dict(self.m.TRACE_MADE)),
                                 ([3], {"jobs": 3, "sha256": made}))
            self.assertIn("does not say the --jobs it was made with: tracing again",
                          said.getvalue())
        _o, report, _runs, record, _c = self._planted(self.LINES, lambda _m, _mutant: False)
        self.assertIn("trace eeeeeeeeeeeeeeee made at --jobs 2, the control run at --jobs 1",
                      report)
        self.assertEqual((record["trace_jobs"], record["control_jobs"]), (2, 1))

    def test_a_record_names_its_trace_and_a_part_prints_none(self):
        # Recheck of 5e, K1 (TJ-4430): "reproducible for one saved trace" could
        # not be checked from a record, which named no trace, and a --shard K/N
        # part printed a record as if it were the whole run. The record names
        # the saved trace's sha256; a part prints none (a record is a whole
        # run's; --jobs runs one in parallel); a trace in no file refuses one.
        def never(_m, _mutant):
            return False
        _o, _r, _runs, record, _c = self._planted(self.LINES, never)
        self.assertEqual(record["trace"], "e" * 64)
        out, report, _runs, record, _c = self._planted(self.LINES, never, shard=(0, 2))
        self.assertIsNone(record)
        self.assertIn("no record: a part of the sample (--shard 1/2): only a whole run is "
                      "recorded", out)
        self.assertIn("(sample 2, seed 0, part 1 of 2)", report)
        with mock.patch.dict(self.m.TRACE_MADE, {"jobs": 2, "sha256": ""}):
            self.assertEqual(self.m._why_no_record("", (), 2, dict.fromkeys(
                ("unconfirmed", "crashed"), 0), []), "the trace is in no saved file")

    def test_a_copy_has_a_git_store_of_its_own(self):
        # Recheck of 5d, J3 (TJ-4351): a copy left git's folder out, so a module
        # that runs git on the tree (test_parser_check's checkout) failed in
        # every copy and was set aside, its kills lost. A copy gets a store of
        # its own that sees it as the tree's git sees the tree: its index (a
        # file staged, a mutant a change), its HEAD and its refs, the objects
        # read from the tree's store, which the copy never writes to.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as out:
            tree = Path(td, "tree")
            for rel in ("src/pkg/a.py", "tests/test_a.py"):
                Path(tree, rel).parent.mkdir(parents=True, exist_ok=True)
                Path(tree, rel).write_text("A = 1\n")
            Path(tree, ".gitignore").write_text("__pycache__/\n")
            subprocess.run(["git", "init", "-q", str(tree)], check=True)
            subprocess.run(["git", "-C", str(tree), "add", "src/pkg/a.py"], check=True)

            def git(where, *args):
                return subprocess.run(["git", "-C", str(where), *args], capture_output=True,
                                      text=True).stdout
            with mock.patch.object(self.m, "REPO", str(tree)), \
                    mock.patch.object(self.m, "PY", sys.executable):
                copy = self.m._make_copy(str(Path(out, "tree")))
            self.assertTrue(Path(copy, ".git").is_dir())
            for args in (("ls-files",), ("status", "--porcelain"), ("symbolic-ref", "HEAD")):
                self.assertEqual(git(copy, *args), git(tree, *args), args)
            self.assertIn("A  src/pkg/a.py", git(copy, "status", "--porcelain"))
            Path(copy, "src/pkg/a.py").write_text("A = 2\n")
            self.assertIn("AM src/pkg/a.py", git(copy, "status", "--porcelain"))
            self.assertIn("A  src/pkg/a.py", git(tree, "status", "--porcelain"))
            # A folder that is no checkout of its own gives a copy without git.
            plain = Path(td, "plain")
            Path(plain, "src").mkdir(parents=True)
            with mock.patch.object(self.m, "REPO", str(plain)), \
                    mock.patch.object(self.m, "PY", sys.executable):
                copy = self.m._make_copy(str(Path(out, "plain")))
            self.assertFalse(Path(copy, ".git").exists())
            # This tree's HEAD, refs and history, when it is a checkout.
            if _commit_resolves("HEAD"):
                dest = Path(out, "here")
                dest.mkdir()
                self.assertTrue(self.m._copy_git(str(dest)))
                for args in (("rev-parse", "HEAD", "HEAD^{tree}"), ("tag", "--list"),
                             ("for-each-ref", "--format=%(objectname) %(refname)")):
                    self.assertEqual(git(dest, *args), git(REPO, *args), args)
                common = git(REPO, "rev-parse", "--path-format=absolute",
                             "--git-common-dir").strip()
                self.assertEqual(Path(dest, ".git/objects/info/alternates").read_text(),
                                 str(Path(common, "objects")) + "\n")

    def test_git_location_variables_never_reach_a_copy(self):
        # Recheck of 5e, K4 (TJ-4433): a harness started with GIT_DIR or
        # GIT_INDEX_FILE set (from a git hook) would have pointed the copies'
        # git, and its own, at another store and index. Started with them set
        # to a decoy repository, the harness's git and a copy's read the copy,
        # and the decoy's index is not written.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as out:
            tree, decoy = Path(td, "tree"), Path(td, "decoy")
            for root, rel in ((tree, "src/pkg/a.py"), (decoy, "src/pkg/other.py")):
                Path(root, rel).parent.mkdir(parents=True)
                Path(root, rel).write_text("A = 1\n")
                Path(root, ".gitignore").write_text("__pycache__/\n")
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "add", rel], check=True)
            index = Path(decoy, ".git", "index").read_bytes()
            git_dir = str(Path(decoy, ".git"))
            planted = {"GIT_DIR": git_dir, "GIT_WORK_TREE": str(decoy),
                       "GIT_INDEX_FILE": str(Path(git_dir, "index")), "GIT_COMMON_DIR": git_dir,
                       "GIT_OBJECT_DIRECTORY": str(Path(git_dir, "objects"))}
            with mock.patch.dict(os.environ, planted):
                m = _harness()                          # its TEST_ENV made under them
                self.assertEqual(set(planted) & set(m.TEST_ENV), set())
                with mock.patch.object(m, "REPO", str(tree)), \
                        mock.patch.object(m, "PY", sys.executable):
                    copy = m._make_copy(str(Path(out, "tree")))
                    env = m._env_for(copy)
                files = subprocess.run(["git", "-C", copy, "ls-files"], env=env,
                                       capture_output=True, text=True).stdout
            self.assertTrue(Path(copy, ".git").is_dir())
            self.assertEqual(files, "src/pkg/a.py\n")
            self.assertEqual(Path(decoy, ".git", "index").read_bytes(), index)
        # Every variable git itself names as a repository's place is dropped.
        local = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True,
                               text=True, env=self.m._git_env())
        if local.returncode == 0:
            self.assertEqual(set(local.stdout.split()) - self.m.GIT_LOCATION_VARS, set())


class TestMutationRuns(unittest.TestCase):

    def test_the_runs_file_holds_its_keys_to_the_registry(self):
        runs = json.loads(RUNS.read_text(encoding="utf-8"))
        self.assertIsInstance(runs, list)
        unknown = []

        def resolves(entry):
            got = _tree_resolves(entry)
            if got is None:
                unknown.append(entry)
            return got
        for entry in runs:
            self.assertEqual(_run_problems(entry, resolves), [], entry)
        good = {"parser": "coinbase", "commit": "3e1e201a", "tree": "a" * 40, "sites": 139,
                "n": 150, "seed": 0, "sampled": 139, "killed": 117, "timeouts": 2,
                "cut_misses": 1, "survived": 15, "equivalent": 4,
                "set_aside": ["test_ci_scripts_and_workflows"],
                "set_aside_per_mutant": ["test_cli_polish"],
                "flagged": ["src/taxjson/lib/brokerages/coinbase.py:928"], "lost": 0,
                "unflushed": 2, "trace": "c" * 64, "trace_jobs": 3, "control_jobs": 3,
                "triage": "TJ-4321"}

        def ok(_entry):                         # the tree resolves
            return True
        self.assertEqual(_run_problems(good, ok), [])
        # The cash readers' row is gone with the readers (step 16 commit 11).
        self.assertIn("no registry id",
                      " ".join(_run_problems(dict(good, parser="cash_readers", sites=209), ok)))
        for change, said in (({"parser": "kraken_ledgers"}, "no registry id"),
                             ({"n": 151, "sites": 400, "sampled": 151, "killed": 130},
                              "1 to 150"),
                             ({"sampled": 0, "killed": 0, "timeouts": 0, "survived": 0,
                               "equivalent": 0}, "more than 0"),
                             ({"n": 100}, "no more than either"),
                             ({"killed": 0, "timeouts": 0, "survived": 0, "equivalent": 0},
                              "of 139 sampled"),
                             ({"killed": 120}, "of 139 sampled"),
                             ({"triage": ""}, "need the triage"),
                             ({"survived": 0, "equivalent": 19, "triage": ""}, "need the triage"),
                             ({"survived": 0, "equivalent": 0, "killed": 136, "triage": ""},
                              "need the triage"),
                             ({"seed": -1}, "whole numbers"),
                             ({"unflushed": -1}, "whole numbers"),
                             ({"lost": 1}, "is not recorded"),
                             ({"trace_jobs": 0}, "the --jobs the run's trace"),
                             ({"control_jobs": None}, "the --jobs the run's trace"),
                             ({"commit": "main"}, "no commit id"),
                             ({"tree": "3e1e201a"}, "no tree id"),
                             ({"set_aside": "m1"}, "no list of names"),
                             ({"set_aside_per_mutant": [""]}, "no list of names"),
                             ({"trace": "c" * 16}, "no sha256 of the saved trace"),
                             ({"flagged": ["coinbase.py"]}, "a survivor's path:line"),
                             ({"survived": 0, "equivalent": 0, "killed": 136,
                               "flagged": ["src/x.py:1"]}, "1 flagged of 0 survivors")):
            found = _run_problems(dict(good, **change), ok)
            self.assertTrue(any(said in f for f in found), (change, found))
        self.assertIn("is no tree of this repository", _run_problems(good, lambda _e: False)[0])
        self.assertIn("keys", _run_problems({"parser": "ib"})[0])
        # Review G2: the tree is checked where the object store holds it. The
        # store's own HEAD tree resolves; a made-up one is refused in a full
        # clone and, in a shallow one, skips: it never passes.
        git = ["git", "-C", str(REPO)]
        head = subprocess.run(git + ["rev-parse", "HEAD", "HEAD^{tree}"], capture_output=True,
                              text=True)
        if head.returncode == 0:
            sha, tree = head.stdout.split()
            self.assertTrue(_tree_resolves({"commit": sha[:10], "tree": tree}))
            self.assertTrue(_tree_resolves({"commit": "0" * 10, "tree": tree}))  # rebased
            self.assertFalse(_tree_resolves({"commit": sha[:10], "tree": sha}))  # a commit
            # Review H2: a folder's tree is a tree of the store, no commit's.
            docs = subprocess.run(git + ["rev-parse", "HEAD:docs"], capture_output=True,
                                  text=True).stdout.strip()
            self.assertEqual(_tree_resolves({"commit": "0" * 10, "tree": docs}),
                             None if _shallow() else False)
            self.assertEqual(_tree_resolves({"commit": sha[:10], "tree": "e" * 40}),
                             None if _shallow() else False)
        if unknown:
            self.skipTest(f"{len(unknown)} record(s) name a tree this shallow clone lacks: "
                          f"checked in a full clone")


if __name__ == "__main__":
    unittest.main()

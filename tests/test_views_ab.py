"""The views A/B, the shown log and the format log (tests/_views_ab.py,
tests/_shown_log/sitecustomize.py, tests/_fmt_log.py; step 6h): a tree is
the same as itself at both rate sets, byte for byte; every `shown` call is
logged and paired between two trees; a moved number is explained only by
its own changed call (a cut at an exact half, or a carried total within
the moves of its process's earlier changes), printed as BASE spelled it,
one number per call; planted cents, signs, counts, dates, orders,
respellings and spacing are caught; a planted flip of `shown` is cuts at
exact halves, the totals they carry and what they move; the format log
names the prints the flip cannot move. One book (evidence: its views move
cuts under a flip, and its sum file's margin fees are a tie, planted uncut
in a second flipped copy), each dump made once for the module, side by
side: the BASE a copy of this tree whose `shown` is round() itself (the
tree before step 6h(4)'s flip, `plant_round`), the NEW a copy whose `shown`
is half up (`plant_flip`), so `TestViewsAB` reads the same on either side
of the flip (`TestTheFlipsCut` and `TestTheShownLog` pin this tree's
`shown`, half up); the rules on dumps made by hand."""
import contextlib
import copy
import decimal
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP
from pathlib import Path

import _engine
import _golden
import _style
import _views_ab as V
from taxjson.model import money as M
import _paths

HERE = _paths.TESTS
BOOK = "evidence"
# The reports flip's shown (step 6h(4)): half up from the float's repr.
_HALF_UP_SHOWN = '''def shown(x, places):
    """Planted by tests/test_views_ab.py: half up from the repr."""
    if isinstance(x, float):
        return float(float_decimal(x).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
    return round(x, places)


'''
# Both planted bodies are seven lines, so a `shown` call below it in
# money.py (json_money's) is on one line in both trees and pairs.
# The tree before the flip: shown is round() itself, half to even on the
# binary float. The dumps' BASE, whichever side of the flip this tree is on.
_ROUND_SHOWN = '''def shown(x, places):
    """Planted by tests/test_views_ab.py: round() itself, the tree before step 6h(4)."""
    if isinstance(x, float):
        return round(x, places)
    return round(x, places)


'''
# The sum file's fees printed from the uncut float, the call kept on its
# line (the trees' calls still pair): a money format tie.
_FEE_LINE = re.compile(r"^( *)(.*)\{M\.shown\(total_fee, 2\)(:17,\.2f)\}(.*)$", re.M)


def _copy(root: Path) -> Path:
    """A copy of this tree's src under `root`."""
    src = root / "src"
    shutil.copytree(_style.SRC / "taxjson", src / "taxjson", ignore=shutil.ignore_patterns("__pycache__"))
    return src


def _plant_shown(src: Path, body: str) -> None:
    """`src`'s M.shown replaced by `body`."""
    money = src / "taxjson" / "model" / "money.py"
    text, n = re.subn(r"(?ms)^def shown\(.*?(?=^def )", body, money.read_text(encoding="utf-8"), count=1)
    assert n == 1 and "ROUND_HALF_UP" in text and "def float_decimal" in text, "shown not found"
    money.write_text(text, encoding="utf-8")


def plant_round(root: Path) -> Path:
    """A copy of this tree's src under `root` whose M.shown is round()
    itself: the tree before step 6h(4)'s flip, the dumps' BASE."""
    src = _copy(root)
    _plant_shown(src, _ROUND_SHOWN)
    return src


def plant_flip(root: Path, fees: bool = False) -> Path:
    """A copy of this tree's src under `root` whose M.shown is half up;
    with `fees`, its sum file's fees printed from the uncut float (the call
    kept): a money format tie the flip cannot move."""
    src = _copy(root)
    _plant_shown(src, _HALF_UP_SHOWN)
    if fees:
        sums = src / "taxjson" / "bin" / "taxjson_sum_gains.py"
        text, n = _FEE_LINE.subn(r"\1M.shown(total_fee, 2); \2{total_fee\3}\4", sums.read_text(encoding="utf-8"))
        assert n == 1 and "{total_fee:17,.2f}" in text, "the sum file's fees line not found"
        sums.write_text(text, encoding="utf-8")
    return src


def plant_idle(root: Path) -> Path:
    """A copy of this tree's src whose lib/numeric is lib/numbers_ (the
    second review's case): the same bytes out, but the shown log's import
    of it fails, which once left the shim idle (TJ-3364)."""
    src = _copy(root)
    lib = src / "taxjson" / "lib"
    (lib / "numeric.py").rename(lib / "numbers_.py")
    for p in (src / "taxjson").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "taxjson.lib.numeric" in text:
            p.write_text(text.replace("taxjson.lib.numeric", "taxjson.lib.numbers_"), encoding="utf-8")
    return src


def plant_timeline(root: Path) -> Path:
    """A copy of the round() base (plant_round: what the fx_a dump runs)
    whose cost timelines write their cost at 6 places (the second review's
    case): no output the goldens record moves, only the hidden
    work/.*.timeline.json files (TJ-3366)."""
    src = plant_round(root)
    timeline = src / "taxjson" / "lib" / "cost_timeline.py"
    text, n = re.subn(r"round_half_up\(cost, 4\)", "round_half_up(cost, 6)", timeline.read_text(encoding="utf-8"))
    assert n == 1, "the timeline's cost not found"
    timeline.write_text(text, encoding="utf-8")
    return src


def _main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = V.main([str(a) for a in argv])
    return rc, out.getvalue() + err.getvalue()


def _process_of(ctx: str) -> str:
    """A context's process as tests/_views_ab `process_of` reads it, here
    so the hand-made dumps need nothing of the tools they test."""
    i = ctx.rfind("|")
    j = ctx.find(">", i + 1)
    return ctx if j < 0 else ctx[:j]


def _calls(*calls) -> dict:
    """A dump's calls made by hand, in order: each (site, operand, places,
    result), optionally with its step and context (default the sum view's
    first process, "0")."""
    out, q = {}, Counter()
    for c in calls:
        site, x, places, result = c[:4]
        step = c[4] if len(c) > 4 else "sum"
        ctx = c[5] if len(c) > 5 else "0"
        proc = (step, _process_of(ctx))
        out.setdefault(step, {}).setdefault(ctx, {}).setdefault(site, []).append([x, places, result, q[proc]])
        q[proc] += 1
    return out


def _doc(outputs, calls=(), log=(), places=4, src="x", python="3", **book):
    """A dump of one book "b1" with one view, sum, made by hand."""
    rec = {"book": "b1", "views": [{"name": "sum", "label": "sum"}], "error": None,
           "outputs": outputs, "log": list(log), "calls": _calls(*calls), "writers": {},
           "files": [["run --no-input", {"inputs/x.csv": "0"}]], "shim": {"processes": 1, "fmt_sink": 0}}
    rec.update(book)
    return {"format": V.FORMAT, "src": src, "rates": "default", "places": places,
            "python": python, "fmt_log": None, "books": [rec]}


# A cut of the sum view's process: 1.125 at 2 places, round()'s 1.12 in
# BASE, half up's 1.13 in NEW.
CUT_A, CUT_B = ("lib/x.py:7", "1.125", 2, "1.12"), ("lib/x.py:7", "1.125", 2, "1.13")


def _cut(where=(), x="1.125", places=2, base="1.12", new="1.13", site="lib/x.py:7"):
    """(BASE's call, NEW's call) of one cut, of `where` (step, context)."""
    return (site, x, places, base, *where), (site, x, places, new, *where)


def _pair(*pairs):
    """BASE's calls and NEW's from (BASE call, NEW call) pairs."""
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _result(base_outputs, new_outputs, pairs=(), **book):
    """compare's record of book b1, BASE and NEW made by hand."""
    base_calls, new_calls = _pair(*pairs)
    base, new = _doc(base_outputs, base_calls, **book), _doc(new_outputs, new_calls, **book)
    for d in (base, new):
        d["books"] = {b["book"]: b for b in d["books"]}
    return V.compare(base, new)["b1"]


def _rows(base_outputs, new_outputs, pairs=(), **book):
    """[(label, where, what moved, why)] of BASE and NEW made by hand."""
    return [r[:4] for r in _result(base_outputs, new_outputs, pairs, **book)["rows"]]


class TestViewsAB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        base = plant_round(cls.tmp / "base")
        flip, fees = plant_flip(cls.tmp / "flip"), plant_flip(cls.tmp / "fees", fees=True)
        runs = {"a": ["--src", base], "b": ["--src", base],
                "fx_a": ["--src", base, "--rates", "fx4"], "fx_b": ["--src", base, "--rates", "fx4"],
                "flip": ["--src", flip], "flip10": ["--src", flip, "--places", "10"],
                "fees": ["--src", fees], "fees_fmt": ["--src", fees, "--fmt-log"],
                "idle": ["--src", plant_idle(cls.tmp / "idle")],
                "timeline": ["--src", plant_timeline(cls.tmp / "timeline"), "--rates", "fx4"]}
        cls.dumps = {k: cls.tmp / f"{k}.json" for k in runs}

        def run(k):
            return subprocess.run([sys.executable, str(HERE / "_views_ab.py"), "dump", str(cls.dumps[k]),
                                   "--books", BOOK, *map(str, runs[k])],
                                  capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL,
                                  env=dict(os.environ, TAXJSON_WIDTH="0"), check=False)
        with ThreadPoolExecutor(len(runs)) as pool:
            cls.results = dict(zip(runs, pool.map(run, runs)))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def dump(self, key) -> dict:
        r = self.results[key]
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(self.dumps[key].read_text(encoding="utf-8"))

    def write(self, name, doc) -> Path:
        p = self.tmp / name
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def planted(self, name, output, edit, key="flip") -> Path:
        """Dump `key` with one output edited."""
        doc = copy.deepcopy(self.dump(key))
        outputs = doc["books"][0]["outputs"]
        outputs[output] = edit(outputs[output])
        return self.write(name, doc)

    # ------------------------------------------------------------ real dumps
    def test_a_tree_against_itself(self):
        """Two dumps of one tree (the round() base) are the same, byte for
        byte, at each rate set, and their calls pair one to one, each the
        same."""
        for a, b in (("a", "b"), ("fx_a", "fx_b")):
            with self.subTest(rates=a):
                self.dump(a), self.dump(b)
                rc, said = _main("diff", self.dumps[a], self.dumps[b], "--strict")
                self.assertEqual(rc, 0, said)
                self.assertRegex(said, r"evidence: \d+ outputs, same")
                self.assertIn("differences 0: cut 0, carried 0, bytes 0, unexplained 0", said)
                self.assertRegex(said, r"files: [1-9]\d+ digest\(s\) compared after the run and each view; 0 differ")
                calls = V.paired(BOOK, self.dump(a)["books"][0], self.dump(b)["books"][0])
                self.assertGreater(len(calls), 100)
                self.assertEqual({c.label for c in calls}, {"same"})
        # The rate sets differ where a figure is converted.
        rc, said = _main("diff", self.dumps["a"], self.dumps["fx_a"])
        self.assertEqual(rc, 2, said)
        self.assertIn("rates default against fx4", said)
        fx, plain = self.dump("fx_a")["books"][0]["outputs"], self.dump("a")["books"][0]["outputs"]
        self.assertNotEqual(fx, plain)

    def test_the_contexts_that_wrote_each_file(self):
        """The log names the context that wrote each file: a gains file the
        gains tool's, a report its own tool's (through run_to_file's temp),
        a `.sum` the run's own (it writes a captured tool's stdout)."""
        book = self.dump("a")["books"][0]
        wrote = book["writers"]
        for name, tool in (("work/margin_gains.json", "taxjson_gains"), ("reports/fees.rpt", "taxjson_fees"),
                           ("work/margin_gains_wash.json", "taxjson_split_gains")):
            with self.subTest(name):
                self.assertEqual(len(wrote[name]), 1, wrote[name])
                step, ctx = wrote[name][0]
                self.assertEqual(step, V.RUN)
                self.assertRegex(ctx, rf"\A0>{tool}#\d+\Z")
                self.assertEqual(V.process_of(ctx), "0")
        self.assertEqual(wrote["reports/margin.sum"], [[V.RUN, "0"]])
        unnamed = sorted(k for k, v in wrote.items() if not v)
        self.assertEqual(unnamed, [], "a file no context was seen writing")

    def test_a_planted_cent(self):
        """In the flipped dump, a cent more in a file whose writer made a
        changed call (the fees report: its 10.125 cut), in a view's stream
        whose process made one (sum: 4.725), in the gains-wash file's gain,
        raw gain, proceeds and cost, and in every figure of another
        account's `.sum`, is unexplained: no changed call printed it, and
        the old tools said `derived` for the first two (TJ-3372, TJ-3336,
        TJ-3363); a real moved cent there, the margin `.sum`'s fees total
        cut by its own call in the sum tool (a context the run captures,
        TJ-3365), is explained."""
        def cent(line_re, nth=0):
            def edit(text):
                lines = text.splitlines(keepends=True)
                i = next(i for i, line in enumerate(lines) if re.search(line_re, line))
                m = list(re.finditer(r"(\d+)\.(\d\d)(?!\d)", lines[i]))[nth]
                lines[i] = f"{lines[i][:m.start()]}{m[1]}.{int(m[2]) + 1:02d}{lines[i][m.end():]}"
                return "".join(lines)
            return edit

        def gains(text):
            j = json.loads(text)
            t = next(t for t in j["transactions"] if isinstance(t.get("gain"), float) and t["gain"])
            for k in ("gain", "raw_gain", "proceeds", "cost"):
                t[k] = round(t[k] + 0.01, 4)
            return json.dumps(j, indent=2)

        def every(text):
            return re.sub(r"(\d+)\.(\d\d)(?!\d)", lambda m: f"{m[1]}.{int(m[2]) + 1 if m[2] != '99' else 98:02d}",
                          text)
        for what, output, edit, n in (
                ("the fees report's mean", "reports/fees.rpt", cent(r"^ib +CAD", 1), 1),
                ("the sum view's first account", "_views/sum.stdout.txt", cent(r"^hand "), 1),
                ("the gains-wash file", "work/margin_gains_wash.json", gains, 4),
                ("another account's .sum", "reports/margin2.sum", every, None)):
            with self.subTest(what):
                new = self.planted("cent.json", output, edit)
                rc, said = _main("diff", self.dumps["a"], new)
                self.assertEqual(rc, 1, said)
                lines = [line for line in said.splitlines() if line.startswith("  unexplained ")]
                self.assertTrue(lines and all(output in line for line in lines), said)
                if n:
                    self.assertEqual(len(lines), n, said)
                self.assertRegex(said, r"unexplained [1-9]")
                self.assertEqual(_main("ties", self.dumps["a"], new)[0], 1)
                self.assertEqual(_main("diff", self.dumps["a"], new, "--strict")[0], 1)
        # The real move: the margin .sum's fees total, cut by the sum tool.
        rc, said = _main("diff", self.dumps["a"], self.dumps["flip"], "--show", "200")
        self.assertEqual(rc, 0, said)
        self.assertRegex(said, r"cut +reports/margin\.sum\[\d+\]  4\.72 -> 4\.73  "
                               r"\(cut bin/taxjson_sum_gains\.py:\d+ 4\.725\)")

    def test_a_planted_flip(self):
        """A tree whose shown is half up: its calls pair one to one with
        BASE's; each moved figure is a cut at an exact half, the same at 10
        places, or a total carried within its process's earlier changes,
        listed with the cuts it could hold; nothing is unexplained."""
        rc, said = _main("ties", self.dumps["a"], self.dumps["flip"], "--deep", self.dumps["flip10"])
        self.assertEqual(rc, 0, said)
        m = re.search(r"ties: (\d+) cut call\(s\) at \d+ site\(s\) in 1 book\(s\); exact halves (\d+)/(\d+); "
                      r"carried (\d+); unexplained calls 0 \(0 against the flip's rule\); same at 10 places (\d+), file ties 0, "
                      r"not found at 10 "
                      r"places 0; differences (\d+) \(cut (\d+), carried (\d+), bytes 0\), unexplained 0", said)
        self.assertIsNotNone(m, said)
        cuts, halves, of, carried, same, diffs, cut, carried_rows = map(int, m.groups())
        self.assertGreaterEqual(cuts, 1)
        self.assertEqual((halves, of, same), (cuts, cuts, cuts), said)
        self.assertGreaterEqual(cut, 1)
        self.assertGreater(diffs, 0)
        # evidence's sum view: a fees total carried from its accounts' cuts.
        self.assertGreaterEqual(carried, 1)
        self.assertGreaterEqual(carried_rows, 1)
        self.assertRegex(said, r"carried \(each with the cuts it could hold\):\n  \S+ .* could hold: \+\S+:\d+ ")
        self.assertEqual(_main("diff", self.dumps["a"], self.dumps["flip"])[0], 0)
        self.assertEqual(_main("diff", self.dumps["a"], self.dumps["flip"], "--strict")[0], 1)
        # Every call pairs; the changed ones are cuts and carried totals.
        calls = V.paired(BOOK, self.dump("a")["books"][0], self.dump("flip")["books"][0])
        labels = {c.label for c in calls}
        self.assertEqual(labels, {"same", "cut", "carried"})
        for c in calls:
            if c.label == "cut":
                self.assertTrue(V.exact_half(c.base[0], c.places), c.where())
        # Each difference is one of _golden.diff's lines.
        base, flip = (self.dump(d)["books"][0]["outputs"] for d in ("a", "flip"))
        for name in sorted(set(base) & set(flip)):
            a, b = _golden.parsed(name, base[name]), _golden.parsed(name, flip[name])
            self.assertEqual(len(list(V.walk(a, b))), len(_golden.diff(a, b)), name)
        # Calls at a half and none changed: NEW is not the flipped tree,
        # and each such call breaks the flip's rule.
        rc, said = _main("ties", self.dumps["a"], self.dumps["b"])
        self.assertEqual(rc, 1, said)
        self.assertIn("is NEW's tree the flipped one?", said)
        self.assertRegex(said, r"unexplained calls ([1-9]\d*) \(\1 against the flip's rule\)")

    def test_the_format_log(self):
        """Through the format log the flip prints the same bytes, and the
        sum file's fees, printed from an uncut float at a tie (4.725, planted
        uncut by plant_flip with its call kept), are a money format tie:
        ties and fmt fail (TJ-3338)."""
        rc, said = _main("diff", self.dumps["fees"], self.dumps["fees_fmt"], "--strict")
        self.assertEqual(rc, 0, said)
        fees = r"money +bin/taxjson_sum_gains\.py:\d+  f-string total_fee  4\.725 @2  printed 4\.72, half up 4\.73"
        rc, said = _main("ties", self.dumps["a"], self.dumps["fees_fmt"])
        self.assertEqual(rc, 1, said)
        self.assertRegex(said, fees)
        self.assertRegex(said, r"format ties \d+ site\(s\), [1-9]\d* a half-up cut prints otherwise")
        self.assertIn("unexplained 0", said)
        rc, said = _main("fmt", self.dumps["fees_fmt"])
        self.assertEqual(rc, 1, said)
        self.assertRegex(said, fees)
        self.assertRegex(said, r"format ties: \d+ site\(s\) \([1-9]\d* money by name, \d+ other, \d+ reviewed "
                               r"not money\); [1-9]\d* a half-up cut prints otherwise")
        rc, said = _main("fmt", self.dumps["fees"])
        self.assertEqual(rc, 2, said)
        self.assertIn("dumped without --fmt-log", said)

    def test_an_idle_shim_is_refused(self):
        """A tree whose lib/numeric is renamed runs the same, but the shown
        log cannot install: it says so, and the dump refuses the book
        rather than read no calls and no ties (TJ-3364); so is a process
        whose log was cut short, or two processes in one context."""
        r = self.results["idle"]
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"evidence: \d+ outputs, 0 shown call\(s\) in 0 process\(es\); did not run: the "
                                   r"shown log did not install in \d+ process\(es\): tests/_shown_log: not installed: "
                                   r"ModuleNotFoundError: No module named 'taxjson\.lib\.numeric'")
        rc, said = _main("diff", self.dumps["a"], self.dumps["idle"])
        self.assertEqual(rc, 2, said)
        self.assertIn("evidence did not run: the shown log did not install", said)
        # A run with no proc record: the shim was idle.
        P = V._shim_problem

        def proc(view=V.RUN, sink=False, file="1", pctx="0"):
            return [{"kind": "proc", "view": view, "fmt_sink": sink, "file": file, "pctx": pctx},
                    {"kind": "exit", "calls": 0, "file": file}]
        self.assertIn("the shim was idle", P(proc("sum"), False))
        self.assertIsNone(P(proc(), False))
        self.assertIn("no format sink", P(proc(), True))
        # A view's process with no record, or without the sink (TJ-3377).
        run = proc(sink=True)
        self.assertIn("1 step(s) (sum)", P(run, False, [V.RUN, "sum"]))
        self.assertIsNone(P(run + proc("sum", file="2"), False, [V.RUN, "sum"]))
        self.assertIn("1 process(es) had no format sink (sum)", P(run + proc("sum", file="2"), True, [V.RUN, "sum"]))
        # A log cut short: no exit record, or fewer calls than it counted.
        self.assertIn("left no exit record", P(run[:1], False))
        call = {"kind": "shown", "view": V.RUN, "file": "1"}
        self.assertIsNone(P(run[:1] + [call, dict(run[1], calls=1)], False))
        self.assertIn("1 read, 2", P(run[:1] + [call, dict(run[1], calls=2)], False))
        # Two processes of a step in one context: their calls would merge.
        self.assertIn("their calls cannot be told apart", P(run + proc(file="2"), False))
        with self.assertRaisesRegex(V.Refused, "no format sink ran in b1"):
            V._sinks({"fmt_log": "x", "books": {"b1": {"shim": {"processes": 36, "fmt_sink": 1}}}}, "NEW")
        # ties: NEW with no call where BASE has some; fmt: a book with no sink.
        text = "_views/sum.stdout.txt"
        rc, said = _main("ties", self.write("i_base.json", _doc({text: "TOTAL 3.12\n"}, [CUT_A])),
                         self.write("i_new.json", _doc({text: "TOTAL 3.13\n"})))
        self.assertEqual(rc, 2, said)
        self.assertIn("NEW's b1 logged no call where BASE's logged 1: was NEW's shown log idle?", said)
        no_sink = _doc({text: "TOTAL 3.12\n"})
        no_sink["fmt_log"] = {"sites": 1, "modules": 1, "failed": []}
        rc, said = _main("fmt", self.write("i_sink.json", no_sink))
        self.assertEqual(rc, 2, said)
        self.assertIn("no format sink ran in b1", said)

    def test_every_file_is_digested(self):
        """A tree whose cost timelines write 6 places records the same
        outputs, but --strict compares every file under the project after
        the run and each view, and names the timeline (TJ-3366)."""
        rc, said = _main("diff", self.dumps["fx_a"], self.dumps["timeline"], "--strict")
        self.assertEqual(rc, 1, said)
        self.assertIn("differences 0: cut 0, carried 0, bytes 0, unexplained 0", said)
        self.assertIn("file        evidence: work/.margin_gains.timeline.json  after run --no-input: other bytes", said)
        self.assertRegex(said, r"files: \d+ digest\(s\) compared after the run and each view; [1-9]\d* differ")
        self.assertEqual(_main("diff", self.dumps["fx_a"], self.dumps["timeline"])[0], 0)
        # A file in one dump only, at a view.
        base, new = _doc({}), _doc({})
        base["books"][0]["files"].append(["sum", {}])
        new["books"][0]["files"].append(["sum", {"reports/x.rpt": "1"}])
        rc, said = _main("diff", self.write("f_base.json", base), self.write("f_new.json", new), "--strict")
        self.assertEqual(rc, 1, said)
        self.assertIn("file        b1: reports/x.rpt  after sum: in NEW only", said)

    # ------------------------------------------------------------ made by hand
    def test_bytes_beside_a_move(self):
        """An output's text with its explained moves applied must be NEW's:
        float noise, -0.0, an int written as a float or a move under the
        goldens' tolerance beside a cut is a bytes row (TJ-3367); so is a
        moved number respelled (TJ-3378) or the spacing beside it changed
        but where its width realigns a column (TJ-3379)."""
        wash = "work/x_gains_wash.json"
        split = (V.RUN, "0>taxjson_split_gains#0")
        w = {"writers": {wash: [list(split)]}}
        cut = _cut(split)
        for what, before, after in (
                ("all four", '{"denied": 1.12, "gain": 108.945, "fee": 0.0, "n": 5}',
                 '{"denied": 1.13, "gain": 108.94499999999609, "fee": -0.0, "n": 5.0}'),
                ("noise", '{"denied": 1.12, "gain": 108.945}', '{"denied": 1.13, "gain": 108.94499999999609}'),
                ("-0.0", '{"denied": 1.12, "fee": 0.0}', '{"denied": 1.13, "fee": -0.0}'),
                ("5.0", '{"denied": 1.12, "n": 5}', '{"denied": 1.13, "n": 5.0}'),
                ("10th place", '{"denied": 1.12, "cost": 812.7000000001}', '{"denied": 1.13, "cost": 812.7000000002}'),
                ("a JSON number respelled", '{"denied": 1.12}', '{"denied": 1.130}')):
            with self.subTest(what):
                rows = _rows({wash: before}, {wash: after}, [cut], **w)
                self.assertEqual([r[0] for r in rows], ["cut", "bytes"], rows)
                base = self.write("bb_base.json", _doc({wash: before}, [cut[0]], **w))
                new = self.write("bb_new.json", _doc({wash: after}, [cut[1]], **w))
                for cmd in ("diff", "ties"):
                    rc, said = _main(cmd, base, new)
                    self.assertEqual(rc, 1, said)
                    self.assertIn("bytes 1", said)
        text = "_views/sum.stdout.txt"
        toml = "reports/x.toml"
        big = _cut(x="1234.125", base="1234.12", new="1234.13")
        for what, name, before, after, labels in (
                # A column realigned by a moved number's width: not a change.
                ("a column realigned", text, "TOTAL    99.99 CAD\n", "TOTAL   100.00 CAD\n",
                 ["cut"]),
                ("a comma lost", text, "TOTAL 1,234.12 CAD\n", "TOTAL 1234.13 CAD\n", ["unexplained"]),
                ("a third place", text, "TOTAL 1,234.12 CAD\n", "TOTAL 1,234.130 CAD\n", ["unexplained"]),
                ("the space removed", text, "TOTAL 1,234.12 CAD\n", "TOTAL 1,234.13CAD\n", ["cut", "bytes"]),
                ("a tab for two spaces", text, "TOTAL 1,234.12  CAD\n", "TOTAL 1,234.13\tCAD\n",
                 ["cut", "bytes"]),
                ("the last newline dropped", text, "TOTAL 1,234.12 CAD\n", "TOTAL 1,234.13 CAD", ["cut", "bytes"]),
                ("a TOML number respelled", toml, "denied = 1.12\n", "denied = 1.130\n", ["cut", "bytes"])):
            with self.subTest(what):
                pairs = [_cut(x="99.995", base="99.99", new="100.0")] if "realigned" in what else [big]
                if name == toml:
                    pairs = [_cut(split)]
                rows = _rows({name: before}, {name: after}, pairs, writers={toml: [list(split)]})
                self.assertEqual([r[0] for r in rows], labels, rows)

    def test_format_ties_fail_money_or_not(self):
        """fmt and ties fail on a format tie a half-up cut prints otherwise,
        whatever its names, but at a site reviewed as not money, whose
        reason is shown (TJ-3368)."""
        def fmt_doc(name, site, expr, moves=True):
            rec = {"kind": "fmt", "book": "b1", "view": V.RUN, "ctx": "0", "site": site, "how": "f-string",
                   "expr": expr, "money": False, "repr": "1.125", "places": 2, "printed": "1.12",
                   "half_up": "1.13" if moves else "1.12", "moves": moves}
            doc = _doc({"_views/sum.stdout.txt": "x\n"}, log=[rec], shim={"processes": 1, "fmt_sink": 1})
            doc["fmt_log"] = {"sites": 1, "modules": 1, "failed": []}
            return self.write(name, doc)
        for expr in ("cps", "m['per_share']", "_z(b)", "avg_pos", "realized_pl"):
            with self.subTest(expr):
                rc, said = _main("fmt", fmt_doc("g_other.json", "bin/x.py:3", expr))
                self.assertEqual(rc, 1, said)
                self.assertIn(f"other     bin/x.py:3  f-string {expr}  1.125 @2  printed 1.12, half up 1.13", said)
                self.assertIn("1 a half-up cut prints otherwise", said)
        rc, said = _main("fmt", fmt_doc("g_same.json", "bin/x.py:3", "cps", moves=False))
        self.assertEqual(rc, 0, said)
        rc, said = _main("fmt", fmt_doc("g_days.json", "bin/taxjson_sum_gains.py:504", "avg_hold"))
        self.assertEqual(rc, 0, said)
        self.assertIn("not money bin/taxjson_sum_gains.py:504  f-string avg_hold", said)
        self.assertIn("[an average of holding days]", said)
        self.assertIn("(0 money by name, 0 other, 1 reviewed not money); 0 a half-up cut prints otherwise", said)

    def test_a_moved_number_needs_its_own_call(self):
        """A moved number is explained only by a changed call of a process
        that printed it, its BASE and NEW results as BASE and NEW print it,
        one number per call: another cut's values, two cents, +50 units, a
        count, a date, a 4th place, another view's cut, a reordered list
        are unexplained (TJ-3336, TJ-3363); a cut of the run's captured tool
        explains its `.sum` (TJ-3365); a cut's values in another process do
        not explain a gains file's figure (TJ-3371)."""
        text, js = "_views/sum.stdout.txt", "_views/sum.stdout.json"
        own = _cut(x="3.125", base="3.12", new="3.13")
        for what, before, after, pairs, labels in (
                ("a cent its own call printed", "TOTAL 3.12\n", "TOTAL 3.13\n", [own], ["cut"]),
                ("a cent beside another cut", "TOTAL 3.12\n", "TOTAL 3.13\n", [_cut()], ["unexplained"]),
                ("two cents", "TOTAL 3.12\n", "TOTAL 3.14\n", [own, _cut(site="lib/x.py:9")], ["unexplained"]),
                ("+50 units, one cut called 100 times", "TOTAL 3.12\n", "TOTAL 3.62\n", [own] * 100,
                 ["unexplained"]),
                ("one call, two numbers", "a 3.12\nb 3.12\n", "a 3.13\nb 3.13\n", [own], ["cut", "unexplained"]),
                ("two calls, two numbers", "a 3.12\nb 3.12\n", "a 3.13\nb 3.13\n", [own, own], ["cut", "cut"]),
                ("a count", "3 trades\n", "4 trades\n", [own], ["unexplained"]),
                ("a date", "on 2025-03-18\n", "on 2025-03-19\n", [own], ["unexplained"]),
                ("a 4th place", "rate 1.1234\n", "rate 1.1235\n", [own], ["unexplained"]),
                ("a cut of another view", "TOTAL 3.12\n", "TOTAL 3.13\n", [_cut(("fees-sum", "0"), "3.125",
                                                                                 2, "3.12", "3.13")],
                 ["unexplained"]),
                ("lines in another order", "a 1.12\nb 2.50\n", "b 2.50\na 1.12\n", [_cut()], ["unexplained"])):
            with self.subTest(what):
                rows = _rows({text: before}, {text: after}, pairs)
                self.assertEqual([r[0] for r in rows], labels, rows)
        # Only at the number's own places (TJ-3397): a whole-dollar call for
        # a 2-place figure or a JSON number, a 4-place call for a cent.
        dollar = _cut(x="1234.5", places=0, base="1234.0", new="1235.0")
        four = _cut(x="0.12505", places=4, base="0.125", new="0.1251")
        for what, name, before, after, pairs, labels in (
                ("a 0-place call, a 2-place text", text, "TOTAL 1,234.00 CAD\n", "TOTAL 1,235.00 CAD\n", [dollar],
                 ["unexplained"]),
                ("a 0-place call, a JSON number", js, '{"proceeds": 1234.0}', '{"proceeds": 1235.0}', [dollar],
                 ["unexplained"]),
                ("a 4-place call, a 2-place text", text, "fee 0.12\n", "fee 0.13\n", [four], ["unexplained"]),
                ("a 2-place call, a JSON number of 1 place", js, '{"t": 2.49}', '{"t": 2.5}',
                 [_cut(x="2.495", base="2.49", new="2.5")], ["cut"])):
            with self.subTest(what):
                rows = _rows({name: before}, {name: after}, pairs)
                self.assertEqual([r[0] for r in rows], labels, rows)
        why = _rows({text: "a 3.12\nb 3.12\n"}, {text: "a 3.13\nb 3.13\n"}, [own])[1][3]
        self.assertIn("explains another number", why)
        for what, before, after, labels in (
                ("a JSON count", {"n": 3}, {"n": 4}, ["unexplained"]),
                ("a JSON cent", {"t": 1.12}, {"t": 1.13}, ["cut"]),
                ("a JSON cent not its call's", {"t": 10.12}, {"t": 10.13}, ["unexplained"]),
                ("a JSON list in another order", {"r": [1.12, 2.5]}, {"r": [2.5, 1.12]}, ["unexplained"]),
                ("a cut's own values in another order", {"r": [1.12, 1.13]}, {"r": [1.13, 1.12]},
                 ["unexplained"])):
            with self.subTest(what):
                rows = _rows({js: json.dumps(before)}, {js: json.dumps(after)}, [_cut()])
                self.assertEqual([r[0] for r in rows], labels, rows)
                if "order" in what:
                    self.assertIn("reordered", rows[0][3])
        # A file: the calls of the process(es) that wrote it, any context.
        gains, report = "work/x_gains.json", "reports/x.sum"
        run_tool = (V.RUN, "0>taxjson_sum_gains#0")
        for what, writer, where, labels in (
                ("the writer's own context", [V.RUN, "0>taxjson_gains#0"], (V.RUN, "0>taxjson_gains#0"), ["cut"]),
                ("a captured tool's (TJ-3365)", [V.RUN, "0"], run_tool, ["cut"]),
                ("a child process of the writer", [V.RUN, "0>taxjson_gains#0"],
                 (V.RUN, "0>taxjson_gains#0|taxjson.bin._entry:gains#0"), ["cut"]),
                ("another view's process (TJ-3371)", [V.RUN, "0>taxjson_gains#0"], ("sum", "0"), ["unexplained"]),
                ("the writer's parent process (TJ-3371)", [V.RUN, "0>x#0|taxjson.bin._entry:gains#0"], (V.RUN, "0"),
                 ["unexplained"]),
                ("no writer the log saw", None, (V.RUN, "0"), ["unexplained"])):
            with self.subTest(what):
                name = report if "captured" in what else gains
                pair = _cut(where, x="943.125", base="943.12", new="943.13")
                text_ = "TOTAL 943.12\n" if name == report else '{"denied": 943.12}'
                rows = _rows({name: text_}, {name: text_.replace("943.12", "943.13")}, [pair],
                             writers={name: [writer]} if writer else {})
                self.assertEqual([r[0] for r in rows], labels, rows)
        self.assertIn("none the log saw", _rows({gains: '{"g": 1.5}'}, {gains: '{"g": 1.51}'}, [_cut()])[0][3])
        # A filed lock 3 cents up beside its writer's four cuts (TJ-3363).
        lock, close = "_views/files/filed/2024.json", ("close-year --yes", "0")
        cuts = [_cut(close, x=x, base=x[:-1], new=f"{x[:-2]}{int(x[-2]) + 1}", site=f"lib/y.py:{i}")
                for i, x in enumerate(("1.115", "2.225", "3.335", "4.445"))]
        rows = _rows({lock: '{"accounts": {"x": {"proceeds": 100.0}}}'},
                     {lock: '{"accounts": {"x": {"proceeds": 100.03}}}'}, cuts, writers={lock: [list(close)]})
        self.assertEqual([r[0] for r in rows], ["unexplained"], rows)

    def test_a_carried_total(self):
        """A total whose operand is a sum of two changed cuts is carried and
        explains its number, listed in ties with the cuts it could hold; a
        third cent beside it is unexplained; a total moved past the moves of
        its process's earlier changes is an unexplained call, named, and so
        is its number."""
        text = "_views/sum.stdout.txt"
        a, b = _cut(x="1.125", base="1.12", new="1.13"), _cut(x="2.005", base="2.0", new="2.01", site="lib/x.py:9")
        total = (("lib/x.py:12", "3.12", 2, "3.12"), ("lib/x.py:12", "3.1399999999999997", 2, "3.14"))
        before, after = "a 1.12\nb 2.00\nTOTAL 3.12\nfee 0.50\n", "a 1.13\nb 2.01\nTOTAL 3.14\nfee 0.51\n"
        r = _result({text: before}, {text: after}, [a, b, total])
        self.assertEqual([x[0] for x in r["rows"]], ["cut", "cut", "carried", "unexplained"], r["rows"])
        self.assertEqual([c.label for c in sorted(r["calls"], key=lambda c: c.q)], ["cut", "cut", "carried"])
        base, new = (self.write(f"c_{k}.json", _doc({text: t}, _pair(a, b, total)[i]))
                     for i, (k, t) in enumerate((("base", before), ("new", after))))
        rc, said = _main("ties", base, new)
        self.assertEqual(rc, 1, said)
        self.assertIn("lib/x.py:12  3.12 -> 3.1399999999999997 @2 (+0.0199999999999997)  3.12 -> 3.14  "
                      "1 call(s) in sum  could hold: +lib/x.py:9 2.005 (2.0 -> 2.01), +lib/x.py:7 1.125 (1.12 -> 1.13)",
                      said)
        self.assertIn("carried 1; unexplained calls 0 (0 against the flip's rule)", said)
        # Past the bound: 3 cents from two cents' moves.
        far = (("lib/x.py:12", "3.12", 2, "3.12"), ("lib/x.py:12", "3.15", 2, "3.15"))
        r = _result({text: "TOTAL 3.12\n"}, {text: "TOTAL 3.15\n"}, [a, b, far])
        self.assertEqual([x[0] for x in r["rows"]], ["unexplained"], r["rows"])
        bad = [c for c in r["calls"] if c.label == "unexplained"]
        self.assertEqual(len(bad), 1)
        self.assertIn("carried: its operand moved +0.03, past the 0.02", bad[0].why)
        rc, said = _main("ties", self.write("c_far_base.json", _doc({text: "TOTAL 3.12\n"}, _pair(a, b, far)[0])),
                         self.write("c_far_new.json", _doc({text: "TOTAL 3.15\n"}, _pair(a, b, far)[1])))
        self.assertEqual(rc, 1, said)
        self.assertIn("unexplained calls:\n  lib/x.py:12 (sum: 0 #0)  carried: its operand moved +0.03", said)
        # The bound is the cuts', each site and operand once (TJ-3395): a
        # cut called again, or a carried reprint of one, adds nothing.
        again = _cut()
        reprint = (("lib/x.py:10", "1.12", 2, "1.12"), ("lib/x.py:10", "1.13", 2, "1.13"))
        two = (("lib/x.py:12", "3.12", 2, "3.12"), ("lib/x.py:12", "3.14", 2, "3.14"))
        for what, pairs in (("a cut twice", [a, again, two]), ("a reprint", [a, reprint, two])):
            with self.subTest(what):
                calls = sorted(_result({text: "x\n"}, {text: "x\n"}, pairs)["calls"], key=lambda c: c.q)
                self.assertEqual(calls[-1].label, "unexplained", [c.label for c in calls])
                self.assertIn("carried: its operand moved +0.02, past the 0.01 its process's 1 earlier cut(s) moved",
                              calls[-1].why)
        # A carried total's own changes count only in its process.
        other = (("lib/x.py:12", "3.12", 2, "3.12", "fees-sum"), ("lib/x.py:12", "3.14", 2, "3.14", "fees-sum"))
        r = _result({text: "x\n"}, {text: "x\n"}, [a, b, other])
        self.assertEqual([c.label for c in r["calls"] if c.step == "fees-sum"], ["unexplained"])

    def test_the_flip_rule(self):
        """ties holds every call to the flip's rule: BASE's result round() of
        its operand, NEW's the half-up cut of its repr. A half-even shown
        (the `rounding=` left off) fails, the call it moved the wrong way
        and the one it left both named (TJ-3392), and so does a carried
        call whose result is not its operand's cut (TJ-3393), which diff,
        without the rule, takes. diff still holds a cut to one unit away
        from zero, and names a result whose form changed (TJ-3399)."""
        text, js = "_views/sum.stdout.txt", "_views/sum.stdout.json"
        down = _cut(x="3.645", base="3.65", new="3.64")
        stays = _cut(x="6.885", base="6.88", new="6.88", site="lib/x.py:9")
        rc, said = _main("ties", self.write("h_base.json", _doc({text: "a 3.65\nb 6.88\n"}, _pair(down, stays)[0])),
                         self.write("h_new.json", _doc({text: "a 3.64\nb 6.88\n"}, _pair(down, stays)[1])))
        self.assertEqual(rc, 1, said)
        self.assertIn("lib/x.py:7 (sum: 0 #0)  the flip's rule: NEW's 3.645 @2 gave 3.64, half up from its repr "
                      "gives 3.65", said)
        self.assertIn("lib/x.py:9 (sum: 0 #0)  the flip's rule: NEW's 6.885 @2 gave 6.88, half up from its repr "
                      "gives 6.89", said)
        self.assertIn("unexplained calls 2 (2 against the flip's rule)", said)
        self.assertRegex(said, r"differences 1 \(cut 0, carried 0, bytes 0\), unexplained 1;")
        # A cut's size and direction, without the rule: one unit away from zero.
        for what, pair, why in (
                ("down", down, "a cut of -0.01, not one unit away from zero: 3.645 @2 3.65 -> 3.64"),
                ("two units", _cut(new="1.14"), "a cut of +0.02, not one unit away from zero: 1.125 @2 1.12 -> 1.14"),
                ("a negative one up", _cut(x="-1.125", base="-1.12", new="-1.11"),
                 "a cut of +0.01, not one unit away from zero: -1.125 @2 -1.12 -> -1.11")):
            with self.subTest(what):
                r = _result({text: f"a {pair[0][3]}\n"}, {text: f"a {pair[1][3]}\n"}, [pair])
                self.assertEqual([c.why for c in r["calls"]], [why])
                self.assertEqual([x[0] for x in r["rows"]], ["unexplained"], r["rows"])
        # A carried call whose result is not its operand's cut (TJ-3393).
        neg = _cut(x="-545.305", base="-545.3", new="-545.31", site="lib/x.py:5")
        wrong = (("lib/x.py:12", "-545.3", 2, "-545.3"), ("lib/x.py:12", "-545.31", 2, "-545.36"))
        before, after = '{"gross_losses": -545.3}', '{"gross_losses": -545.36}'
        self.assertEqual([x[0] for x in _rows({js: before}, {js: after}, [neg, wrong])], ["carried"])
        rc, said = _main("ties", self.write("w_base.json", _doc({js: before}, _pair(neg, wrong)[0])),
                         self.write("w_new.json", _doc({js: after}, _pair(neg, wrong)[1])))
        self.assertEqual(rc, 1, said)
        self.assertIn("lib/x.py:12 (sum: 0 #0)  the flip's rule: NEW's -545.31 @2 gave -545.36, half up from its "
                      "repr gives -545.31", said)
        self.assertRegex(said, r"differences 1 \(cut 0, carried 0, bytes 0\), unexplained 1;")
        # A result of another form, the same value.
        form = (("lib/x.py:3", "2", 0, "2"), ("lib/x.py:3", "2", 0, "2.0"))
        r = _result({text: "x\n"}, {text: "x\n"}, [form])
        self.assertEqual([(c.label, c.why) for c in r["calls"]], [("unexplained", "its result's form changed: 2 -> 2.0")])
        # The rule's own cases: an int as round() leaves it, a float not
        # finite passed through, an operand of another type broken.
        for what, call, broken in (
                ("an int", ("lib/x.py:3", "7", 0, "7"), ""),
                ("infinity", ("lib/x.py:3", "inf", 2, "inf"), ""),
                ("nan", ("lib/x.py:3", "nan", 2, "nan"), ""),
                ("-0.0", ("lib/x.py:3", "-0.001", 2, "-0.0"), ""),
                ("a Decimal", ("lib/x.py:3", "Decimal('1.125')", 2, "Decimal('1.13')"),
                 "the flip's rule: BASE's operand Decimal('1.125') @2 is not a float or an int")):
            with self.subTest(what):
                c = V.paired("b1", _doc({}, [call])["books"][0], _doc({}, [call])["books"][0])[0]
                self.assertEqual(V.flip_rule(c), broken)

    def test_the_sign_is_kept(self):
        """A JSON or TOML number keeps its sign: a changed sign is
        unexplained, a negative cut is a cut, and both are shown signed
        (TJ-3337); a text's number is its call's as printed, its minus too."""
        js, toml, text = "_views/sum.stdout.json", "_views/files/reports/x.toml", "_views/sum.stdout.txt"
        w = {"writers": {toml: [["sum", "0"]]}}
        neg = _cut(x="-1.125", base="-1.12", new="-1.13")
        for what, name, before, after, pairs, lab, moved in (
                ("-1.12 -> 1.13", js, '{"gain": -1.12}', '{"gain": 1.13}', [_cut()], "unexplained", "-1.12 -> 1.13"),
                ("-5.0 -> 5.01", js, '{"gain": -5.0}', '{"gain": 5.01}', [_cut()], "unexplained", "-5.0 -> 5.01"),
                ("-1.12 -> -1.13", js, '{"gain": -1.12}', '{"gain": -1.13}', [neg], "cut", "-1.12 -> -1.13"),
                ("a positive cut for a negative figure", js, '{"gain": -1.12}', '{"gain": -1.13}', [_cut()],
                 "unexplained", "-1.12 -> -1.13"),
                ("TOML", toml, "a = -1.12\n", "a = 1.13\n", [neg], "unexplained", "-1.12 -> 1.13"),
                ("text, both negative", text, "gain -1.12\n", "gain -1.13\n", [neg], "cut", "-1.12 -> -1.13"),
                ("text, a negative printed unsigned", text, "gain 1.12\n", "gain 1.13\n", [neg], "unexplained",
                 "1.12 -> 1.13"),
                ("text, a positive call under a minus", text, "gain -1.12\n", "gain -1.13\n", [_cut()],
                 "unexplained", "-1.12 -> -1.13"),
                ("text, the sign gone", text, "gain -1.12\n", "gain 1.13\n", [neg], "unexplained", "")):
            with self.subTest(what):
                rows = _rows({name: before}, {name: after}, pairs, **w)
                self.assertEqual([r[0] for r in rows], [lab], rows)
                self.assertIn(moved, rows[0][2])

    def test_bytes(self):
        """An output whose values compare the same but whose text differs
        is a bytes difference: diff and --strict fail (TJ-3339)."""
        js = "_views/sum.stdout.json"
        for what, before, after in (("indent", '{"a": 1.5}', '{\n "a": 1.5\n}'),
                                    ("-0.0", '{"a": 0.0}', '{"a": -0.0}'),
                                    ("float noise", '{"a": 0.3}', '{"a": 0.30000000000000004}')):
            with self.subTest(what):
                base = self.write("b_base.json", _doc({js: before}))
                new = self.write("b_new.json", _doc({js: after}))
                for args in ((), ("--strict",)):
                    rc, said = _main("diff", base, new, *args)
                    self.assertEqual(rc, 1, said)
                    self.assertIn("bytes 1", said)
                self.assertEqual(_main("diff", base, base, "--strict")[0], 0)

    def test_a_file_tie(self):
        """--deep: a cut whose operand at 10 places (its own call there) is
        no half is a file tie, its 10-place operand named; the same operand
        there is "same at 10 places"; neither is "not found at 10 places",
        counted apart; a total whose NEW operand the flip made a half is
        named so; a cut not at a half is an unexplained call (TJ-3340,
        TJ-3342)."""
        before, after = {"_views/sum.stdout.txt": "fees 1.12\n"}, {"_views/sum.stdout.txt": "fees 1.13\n"}
        base_calls, new_calls = _pair(_cut())
        base = self.write("t_base.json", _doc(before, base_calls))
        new = self.write("t_new.json", _doc(after, new_calls))
        for name, calls, says, count in (
                ("file", [("lib/x.py:7", "1.12499996", 2, "1.12")], "file tie (10 places: 1.12499996)",
                 "file ties 1"),
                ("same", [CUT_B], "same at 10 places", "same at 10 places 1"),
                ("same elsewhere at its site", [("lib/x.py:7", "0.5", 2, "0.5"), CUT_B], "same at 10 places",
                 "same at 10 places 1"),
                ("elsewhere", [("lib/y.py:1", "1.12499996", 2, "1.12")], "not found at 10 places",
                 "not found at 10 places 1"),
                ("none", [], "not found at 10 places", "not found at 10 places 1")):
            with self.subTest(name):
                deep = self.write(f"t_{name}10.json", _doc({}, calls, places=10))
                rc, said = _main("ties", base, new, "--deep", deep)
                self.assertEqual(rc, 0, said)
                self.assertIn(f"lib/x.py:7  1.125 @2  1.12 -> 1.13  exact half  {says}  1 call(s)", said)
                self.assertIn(count, said)
                self.assertIn("differences 1 (cut 1, carried 0, bytes 0), unexplained 0", said)
        rc, said = _main("ties", base, new)
        self.assertEqual(rc, 0, said)
        self.assertIn("exact half  -  1 call(s)", said)
        self.assertIn("10 places not checked", said)
        # A total the flip moved onto a half.
        made = (("lib/x.py:9", "2.0", 2, "2.0"), ("lib/x.py:9", "2.005", 2, "2.01"))
        rc, said = _main("ties", self.write("t_m_base.json", _doc({"_views/sum.stdout.txt": "fees 1.12\nt 2.00\n"},
                                                                  _pair(_cut(), made)[0])),
                         self.write("t_m_new.json", _doc({"_views/sum.stdout.txt": "fees 1.13\nt 2.01\n"},
                                                         _pair(_cut(), made)[1])))
        self.assertEqual(rc, 0, said)
        self.assertIn("lib/x.py:9  2.0 -> 2.005 @2 (+0.005)  2.0 -> 2.01  a tie the flip made", said)
        # Not at a half: a bug in shown or the log, which fails diff and
        # ties (ties by the flip's rule, which BASE's result breaks too).
        odd = _cut(x="1.1251")
        r = _result(before, after, [odd])
        self.assertEqual([c.why for c in r["calls"]], ["a cut not at an exact half: 1.1251 @2 1.12 -> 1.13"])
        rc, said = _main("ties", self.write("t_o_base.json", _doc(before, [odd[0]])),
                         self.write("t_odd.json", _doc(after, [odd[1]])))
        self.assertEqual(rc, 1, said)
        self.assertIn("the flip's rule: BASE's 1.1251 @2 gave 1.12, round() gives 1.13", said)
        self.assertIn("unexplained calls 1 (1 against the flip's rule)", said)
        # Calls at a half and none changed; calls changed and nothing moved.
        rc, said = _main("ties", base, self.write("t_still.json", _doc(before, base_calls)))
        self.assertEqual(rc, 1, said)
        self.assertIn("is NEW's tree the flipped one?", said)
        rc, said = _main("ties", base, self.write("t_quiet.json", _doc(before, new_calls)))
        self.assertEqual(rc, 1, said)
        self.assertIn("calls changed and nothing moved", said)

    def test_a_line_more_is_unexplained(self):
        """A text one line longer is a shape change, whatever calls changed:
        its count is no figure."""
        pair = _cut(x="2.5", places=0, base="2.0", new="3.0")
        base = self.write("s_base.json", _doc({"_views/sum.stdout.txt": "fees 1\n"}, [pair[0]]))
        new = self.write("s_new.json", _doc({"_views/sum.stdout.txt": "fees 1\nfees 2\n"}, [pair[1]]))
        rc, said = _main("diff", base, new)
        self.assertEqual(rc, 1, said)
        self.assertIn("unexplained 1", said)

    def test_what_is_refused(self):
        """No books, a name twice (a book or a key) or a book that did not
        run is refused; --strict fails when nothing was compared; a --deep
        dump of another tree, rates or Python, not more places than NEW or
        missing a book is refused (TJ-3323, TJ-3324, TJ-3326, TJ-3331,
        TJ-3341); calls that do not pair are refused, but by --strict,
        which fails on any difference anyway."""
        ok = self.write("r_ok.json", _doc({"_views/sum.stdout.txt": "fees 1.12\n"}))
        empty = _doc({})
        empty["books"] = []
        twice = _doc({"_views/sum.stdout.txt": "fees 1.12\n"})
        twice["books"] *= 2
        failed = _doc({}, error="AssertionError: b1: tjs run exited 1, expected 0")
        for name, doc, why in (("r_empty.json", empty, "no books"), ("r_twice.json", twice, "book 'b1' twice"),
                               ("r_failed.json", failed, "b1 did not run: AssertionError"),
                               ("r_py.json", _doc({}, python="4"), "python 3 against 4")):
            with self.subTest(name):
                rc, said = _main("diff", ok, self.write(name, doc), "--strict")
                self.assertEqual(rc, 2, said)
                self.assertIn(why, said)
        key_twice = self.tmp / "r_key.json"
        key_twice.write_text(ok.read_text(encoding="utf-8").replace(
            '"outputs": {', '"outputs": {"_views/sum.stdout.txt": "fees 9.99\\n", '), encoding="utf-8")
        rc, said = _main("diff", ok, key_twice)
        self.assertEqual((rc, "'_views/sum.stdout.txt' twice" in said), (2, True), said)
        nothing = self.write("r_nothing.json", _doc({}))
        rc, said = _main("diff", nothing, nothing, "--strict")
        self.assertEqual(rc, 1, said)
        self.assertIn("nothing was compared", said)
        self.assertEqual(_main("diff", ok, ok, "--strict")[0], 0)
        other_book = _doc({}, places=10)
        other_book["books"][0]["book"] = "b2"
        for name, deep, why in (("r_deep4.json", _doc({}), "places 4 against NEW's 4"),
                                ("r_deep_src.json", _doc({}, places=10, src="y"), "src y against NEW's x"),
                                ("r_deep_py.json", _doc({}, places=10, python="4"), "python 4 against NEW's 3"),
                                ("r_deep_book.json", other_book, "no b1")):
            with self.subTest(name):
                rc, said = _main("ties", ok, ok, "--deep", self.write(name, deep))
                self.assertEqual(rc, 2, said)
                self.assertIn(why, said)
        rc, said = _main("dump", self.tmp / "r_x.json", "--books", "no-such-book")
        self.assertEqual(rc, 2, said)
        self.assertIn("the project books are", said)
        # Calls that do not pair.
        out = {"_views/sum.stdout.txt": "fees 1.12\n"}
        for name, base_calls, new_calls, why in (
                ("one tree only", [CUT_A], [CUT_A, ("lib/y.py:1", "1.0", 2, "1.0")],
                 "lib/y.py:1 (sum: 0): a call in NEW only"),
                ("another count", [CUT_A, CUT_A], [CUT_A], "lib/x.py:7 (sum: 0): another count at a site: "
                                                           "2 in BASE, 1 in NEW"),
                ("other places", [CUT_A], [("lib/x.py:7", "1.125", 3, "1.125")], "places 2 in BASE, 3 in NEW")):
            with self.subTest(name):
                base = self.write("p_base.json", _doc(out, base_calls))
                new = self.write("p_new.json", _doc(out, new_calls))
                for cmd in ("diff", "ties"):
                    rc, said = _main(cmd, base, new)
                    self.assertEqual(rc, 2, said)
                    self.assertIn(f"the calls do not pair: {why}" if "places" not in why else why, said)
                rc, said = _main("diff", base, new, "--strict")
                self.assertEqual(rc, 0, said)
                self.assertIn("b1: not paired: b1: the calls do not pair", said)


class TestTheFlipsCut(unittest.TestCase):
    """The rule's cut (`half_up`, `_rounded`) is this tree's `M.shown` on a
    table of operands, 1e26, the float maximum and -0.0 among them, at 0,
    2, 4 and 6 places: the tool mirrors shown's context (wide, nothing
    trapped) rather than run the tree it judges (TJ-3403)."""

    def test_half_up_is_shown(self):
        table = (2.675, -2.675, 0.125, -0.125, 1.005, 0.015, 0.005, -0.005, 1.5, 0.5, -0.5, 2.5, 4.725,
                 33.20865, 0.00015, 38.3569355, 5e-07, -5e-07, 2.0000025, 12345.675, -12345.675, 1234567.125,
                 7295.955, 14780.185, 99999999.995, 123456789012.345, 1e26, 1e22, 1.7976931348623157e308,
                 5e-324, 1e16 + 2, 1.2345678901234568e+16, 0.0, -0.0, -0.001, 0.001, -0.004999)
        for x in table:
            for places in (0, 2, 4, 6):
                with self.subTest(x=x, places=places):
                    want = repr(M.shown(x, places))
                    self.assertEqual(repr(V.half_up(repr(x), places)), want)
                    self.assertEqual(V._rounded(repr(x), places, ROUND_HALF_UP), want)
                    self.assertEqual(V._rounded(repr(x), places, None), repr(round(x, places)))
        for x in (7, -7, 0, 10 ** 30, math.inf, -math.inf):
            with self.subTest(x=x):
                self.assertEqual(V._rounded(repr(x), 2, ROUND_HALF_UP), repr(M.shown(x, 2)))
        self.assertEqual(V._rounded("nan", 2, ROUND_HALF_UP), repr(M.shown(math.nan, 2)))
        # Under a context too narrow to quantize 1e26 at 2 places, and
        # trapping InvalidOperation, as the default 28 digits did (TJ-3403).
        with decimal.localcontext() as ctx:
            ctx.prec = 5
            ctx.traps[decimal.InvalidOperation] = True
            self.assertEqual(V.half_up("1e+26", 2), 1e26)
            self.assertEqual(V.half_up("12345.675", 2), 12345.68)
            self.assertEqual(V._rounded("1e+26", 2, ROUND_HALF_UP), "1e+26")


class TestTheShownLog(unittest.TestCase):
    def run_py(self, code, td, **env_):
        env = {k: v for k, v in os.environ.items() if k not in (V.CTX, V.PLACES, V.ROOT)}
        env.update({V.LOG: str(Path(td) / "log"), V.BOOK: "b1", V.VIEW: "sum"}, **env_)
        env["PYTHONPATH"] = os.pathsep.join([str(_style.SRC), str(V.SHIM)])
        r = subprocess.run([sys.executable, "-c", code], cwd=td, env=env, capture_output=True,
                           text=True, timeout=120, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout, V._read_log(Path(td) / "log")

    def test_it_records_every_call_and_leaves_shown_alone(self):
        """In a taxjson process the shim wraps shown: every call is one
        record (its context, site, ordinal there, operand, places and
        result), the value returned unchanged; the process's proc and exit
        records count them; with the gains places set, round_floats writes
        at those places."""
        with tempfile.TemporaryDirectory() as td:
            out, recs = self.run_py(
                "from taxjson.model import money as M\n"
                "print(M.shown(2.675, 2), M.shown(1.5, 0), M.shown(3.14159, 2), "
                "M.round_floats({'gain': 1.23456789012}))\n"
                "for x in (1.005, 2.5): M.shown(x, 2)\n", td, **{V.PLACES: "10"})
        # 2.675 and 1.005 are exact halves at 2 places (their reprs), cut up
        # since step 6h(4)'s flip (round() gave 2.67 and 1.0); 1.5 at 0
        # places is a half round() moved up already.
        self.assertEqual(out.split()[:3], ["2.68", "2.0", "3.14"])
        self.assertIn("'gain': 1.2345678901", out)
        calls = [(x["ctx"], x["site"], x["n"], x["repr"], x["places"], x["result"]) for x in recs
                 if x["kind"] == "shown"]
        self.assertEqual(calls, [("0", "<string>:2", 0, "2.675", 2, "2.68"), ("0", "<string>:2", 1, "1.5", 0, "2.0"),
                                 ("0", "<string>:2", 2, "3.14159", 2, "3.14"), ("0", "<string>:3", 0, "1.005", 2, "1.01"),
                                 ("0", "<string>:3", 1, "2.5", 2, "2.5")])
        self.assertEqual({(x["book"], x["view"]) for x in recs}, {("b1", "sum")})
        self.assertEqual([(x["fmt_sink"], x["pctx"]) for x in recs if x["kind"] == "proc"], [(False, "0")])
        self.assertEqual([x["calls"] for x in recs if x["kind"] == "exit"], [5])
        self.assertIsNone(V._shim_problem(recs, False, ["sum"]))

    def test_it_says_when_it_cannot_install(self):
        """A taxjson on the path that lacks what the shim wraps: said on
        stderr and recorded as an error, never idle in silence (TJ-3364)."""
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / "fake" / "taxjson"
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            env = dict(os.environ, **{V.LOG: str(Path(td) / "log"), V.BOOK: "b1", V.VIEW: "sum"})
            env["PYTHONPATH"] = os.pathsep.join([str(pkg.parent), str(V.SHIM)])
            r = subprocess.run([sys.executable, "-c", "print('ran')"], cwd=td, env=env, capture_output=True,
                               text=True, timeout=120, check=False)
            recs = V._read_log(Path(td) / "log")
        self.assertEqual((r.returncode, r.stdout), (0, "ran\n"), r.stderr)
        self.assertIn("tests/_shown_log: not installed: ModuleNotFoundError", r.stderr)
        self.assertEqual([x["kind"] for x in recs], ["error"])
        self.assertIn("not installed", V._shim_problem(recs, False))

    def test_a_tree_from_before_step_8j(self):
        """A views A/B's base may be a tree from before step 8j, whose
        run_cmd is lib/dispatch's: the shim wraps that one (TJ-3541)."""
        import shutil
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / "old"
            shutil.copytree(_style.SRC / "taxjson", old / "taxjson",
                            ignore=shutil.ignore_patterns("__pycache__"))
            run = old / "taxjson" / "pipeline" / "dispatch.py"
            body = "".join(ln for ln in run.read_text("utf-8").splitlines(keepends=True)
                           if not ln.startswith("from taxjson.lib.dispatch import"))
            lib = old / "taxjson" / "lib" / "dispatch.py"
            lib.write_text(lib.read_text("utf-8") + "\n" + body, encoding="utf-8")
            run.unlink()
            env = {k: v for k, v in os.environ.items() if k not in (V.CTX, V.PLACES, V.ROOT)}
            env.update({V.LOG: str(Path(td) / "log"), V.BOOK: "b1", V.VIEW: "sum"})
            env["PYTHONPATH"] = os.pathsep.join([str(old), str(V.SHIM)])
            r = subprocess.run([sys.executable, "-c",
                                "import taxjson.lib.dispatch as d, importlib.util as u; "
                                "print(d.run_cmd.__name__, u.find_spec('taxjson.pipeline.dispatch'))"],
                               cwd=td, env=env, capture_output=True, text=True, timeout=120, check=False)
            recs = V._read_log(Path(td) / "log")
            self.assertEqual((r.returncode, r.stdout), (0, "tool_run_cmd None\n"), r.stderr)
            self.assertNotIn("not installed", r.stderr)
            self.assertEqual([x["kind"] for x in recs], ["proc", "exit"])
            # A module a current pipeline/dispatch misses is named, never
            # taken for an old tree (TJ-3550).
            run.write_text("import taxjson.lib.zz_gone\n", encoding="utf-8")
            env[V.LOG] = str(Path(td) / "log2")
            r = subprocess.run([sys.executable, "-c", "print('ran')"], cwd=td, env=env,
                               capture_output=True, text=True, timeout=120, check=False)
        self.assertIn("not installed: ModuleNotFoundError: No module named 'taxjson.lib.zz_gone'",
                      r.stderr)

    def test_the_four_place_writers_are_widened(self):
        """The places hook widens the cost timeline's 4-place money (its
        `round_half_up(x, 4)`, lib/numeric's, imported when it writes) and
        the writers WIDENED names, each by its call's source (TJ-3340,
        TJ-3370), and no other caller's."""
        timeline = (_style.SRC / "taxjson" / "lib" / "cost_timeline.py").read_text(encoding="utf-8")
        self.assertIn("from taxjson.lib.numeric import round_half_up", timeline)
        self.assertRegex(timeline, r"round_half_up\(cost, 4\)")
        code = ("from taxjson.lib import numeric; "
                "ns = {}; exec(compile('from taxjson.lib.numeric import round_half_up as r; x = r(1.23456789, 4)', "
                "'/x/taxjson/lib/cost_timeline.py', 'exec'), ns); "
                "print(ns['x'], numeric.round_half_up(1.23456789, 4))")
        with tempfile.TemporaryDirectory() as td:
            out, _recs = self.run_py(code, td, **{V.PLACES: "6"})
            self.assertEqual(out.split(), ["1.234568", "1.2346"])
            out, _recs = self.run_py(code, td)
            self.assertEqual(out.split(), ["1.2346", "1.2346"])
        shim = (V.SHIM / "sitecustomize.py").read_text(encoding="utf-8")
        ns = {}
        exec(re.search(r"(?ms)^WIDENED = \{.*?^\}", shim)[0], {"os": os}, ns)
        package = [p.read_text(encoding="utf-8") for p in sorted((_style.SRC / "taxjson").rglob("*.py"))]
        for name, (module, at, calls) in ns["WIDENED"].items():
            # A module's calls are in it; the engine's (None: any module, its
            # modules move, PLAN.md step 10) in its text, one site each in
            # the package, so no other caller is widened.
            src = (_style.SRC / "taxjson" / module).read_text(encoding="utf-8") if module \
                else _engine.source()
            for call in calls:
                with self.subTest(call):
                    self.assertIn(call, src)
                    if module is None:
                        self.assertEqual(sum(text.count(call) for text in package), 1)
        # Each by its call's source: the close book's cost, not its units
        # on the same line; the §263(h) cost, not another 4-place present
        # (in any module of a taxjson package: its text is one site in the
        # package, asserted above), and nothing outside the package.
        code = ("from taxjson.model import money as M\n"
                "src = ('h, avg = 1.23456789, 1.0\\n'\n"
                "       'd = {\"units\": M.units(h, 6), \"cost\": M.units(h * avg, 6)}\\n'\n"
                "       '_cap_263h = M.of(1.23456789)\\n'\n"
                "       'e = (M.present(_cap_263h, 4), M.present(_cap_263h * 1, 4))\\n')\n"
                "import linecache\n"
                "for path in ('/x/taxjson/lib/fx_cash_v2.py', '/x/taxjson/engine/us/trades.py', '/x/other/m.py'):\n"
                "    linecache.cache[path] = (len(src), None, src.splitlines(True), path)\n"
                "    ns = {'M': M}; exec(compile(src, path, 'exec'), ns)\n"
                "    print(path.rsplit('/', 1)[1], ns['d']['units'], ns['d']['cost'], *ns['e'])\n")
        with tempfile.TemporaryDirectory() as td:
            out, _recs = self.run_py(code, td, **{V.PLACES: "8"})
            self.assertEqual(out.split("\n")[:3], ["fx_cash_v2.py 1.234568 1.23456789 1.23456789 1.2346",
                                                   "trades.py 1.234568 1.234568 1.23456789 1.2346",
                                                   "m.py 1.234568 1.234568 1.2346 1.2346"])
            out, _recs = self.run_py(code, td)
            self.assertEqual(out.split("\n")[0], "fx_cash_v2.py 1.234568 1.234568 1.2346 1.2346")
        # A call made through an alias is told by its columns too.
        alias = code.replace("'d = {\"units\": M.units(h, 6), \"cost\": M.units(h * avg, 6)}\\n'",
                             "'u = M.units\\nd = {\"units\": u(h, 6), \"cost\": u(h, 6)}  # M.units(h * avg, 6)\\n'")
        self.assertNotEqual(alias, code)
        with tempfile.TemporaryDirectory() as td:
            out, recs = self.run_py(alias, td, **{V.PLACES: "8"})
            self.assertEqual(out.split("\n")[0], "fx_cash_v2.py 1.234568 1.234568 1.23456789 1.2346")
            self.assertIsNone(V._shim_problem(recs, False, ["sum"]))
        # A call it cannot tell is an error record, not widened: the dump
        # refuses the book (TJ-3394).
        blind = "import sitecustomize\nsitecustomize._call_text = lambda frame, name: None\n"
        with tempfile.TemporaryDirectory() as td:
            out, recs = self.run_py(blind + code, td, **{V.PLACES: "8"})
            self.assertEqual(out.split("\n")[0], "fx_cash_v2.py 1.234568 1.234568 1.2346 1.2346")
            problem = V._shim_problem(recs, False, ["sum"])
            # An error record for each widened call it could not tell (the
            # message counts the records, three here, not processes).
            self.assertIn("--places could not tell a call in 3 process(es): tests/_shown_log: --places: cannot tell "
                          "which call of M.units runs at lib/fx_cash_v2.py:2 (Python ", problem)

    def test_a_process_that_dies_is_refused(self):
        """A child that ends by os._exit after its calls leaves its proc
        record (flushed at once) and no exit record: refused, its calls
        lost; so are an empty log file, a line cut off mid-write (named, no
        traceback: TJ-3398), a log file with no proc record and more
        claimed child contexts than proc records (TJ-3396)."""
        code = ("import os, subprocess, sys\n"
                "import taxjson.model.money as M\n"
                "M.shown(1.125, 2)\n"
                "subprocess.run([sys.executable, '-c', "
                "'import os, taxjson.model.money as M; M.shown(2.675, 2); os._exit(0)'])\n")
        with tempfile.TemporaryDirectory() as td:
            _out, recs = self.run_py(code, td)
            claims = len(list((Path(td) / "log" / "claims").glob("*.*")))
        self.assertEqual(sorted(r["pctx"] for r in recs if r["kind"] == "proc"), ["0", "0|-c#0"])
        self.assertEqual(claims, 1)
        self.assertIn("1 process(es) left no exit record or fewer calls than they made (sum: 0 read, no exit)",
                      V._shim_problem(recs, False, ["sum"], claims))
        head = {"kind": "proc", "pctx": "0", "fmt_sink": False, "book": "b1", "view": "sum", "ctx": "0"}
        whole = (json.dumps(head) + "\n" + json.dumps(["s", "0", "lib/x.py:7", "1.125", 2, "1.12"]) + "\n"
                 + json.dumps({"kind": "exit", "calls": 1, "book": "b1", "view": "sum", "ctx": "0"}) + "\n")
        with tempfile.TemporaryDirectory() as td:
            log = Path(td)
            (log / "1.1.jsonl").write_text(whole, encoding="utf-8")
            self.assertIsNone(V._shim_problem(V._read_log(log), False, ["sum"]))
            self.assertIn("1 child process(es) claimed a context and 0 left a proc record",
                          V._shim_problem(V._read_log(log), False, ["sum"], 1) or "")
            for what, text, says in (
                    ("empty", "", "1 log line(s) empty or cut off (2.2.jsonl line 1: 'an empty log')"),
                    ("cut off", whole[:-30], "1 log line(s) empty or cut off (2.2.jsonl line 3: "),
                    ("a line that is no record", whole.replace('"s", ', '"t", '), "(2.2.jsonl line 2: "),
                    ("no proc record", whole.split("\n", 1)[1], "1 log file(s) with no proc record (2.2.jsonl)")):
                with self.subTest(what):
                    (log / "2.2.jsonl").write_text(text, encoding="utf-8")
                    self.assertIn(says, V._shim_problem(V._read_log(log), False, ["sum"]) or "")

    def test_it_names_the_context_that_wrote_a_file(self):
        """A file a tool's process writes is that context's; a child
        process's context is its parent's, its module and its rank among
        that context's children of the module; one written to a tool's
        stdout (run_to_file's temp, by its inode) and renamed into place is
        that tool's, not its caller's (TJ-3336)."""
        code = (
            "import os, sys; from taxjson.pipeline import dispatch as D\n"
            "os.makedirs('proj/work', exist_ok=True)\n"
            "with open('proj/work/mine.json', 'w') as f: f.write('{}')\n"
            "child = \"open('proj/work/child.json', 'a').write('{}')\"\n"
            "D.run_cmd([sys.executable, '-c', child], capture_output=True)\n"
            "D.run_cmd([sys.executable, '-c', child], capture_output=True)\n"
            "with open('proj/work/.out.part', 'w') as f:\n"
            "    D.run_cmd([sys.executable, '-c', 'print(1)'], stdout=f)\n"
            "os.replace('proj/work/.out.part', 'proj/work/out.json')\n")
        with tempfile.TemporaryDirectory() as td:
            _out, recs = self.run_py(code, td, **{V.ROOT: str(Path(td) / "proj")})
        exe = os.path.basename(sys.executable)
        ctxs = [r["id"] for r in recs if r["kind"] == "ctx"]
        self.assertEqual(ctxs, [f"0>{exe}#0", f"0>{exe}#1", f"0>{exe}#2"], recs)
        self.assertEqual(sorted(r["pctx"] for r in recs if r["kind"] == "proc"),
                         ["0", f"0>{exe}#0|-c#0", f"0>{exe}#1|-c#0", f"0>{exe}#2|-c#0"])
        wrote = V.writers([dict(r, view="run --no-input") for r in recs],
                          ["work/mine.json", "work/child.json", "work/out.json"])
        self.assertEqual(wrote, {"work/mine.json": [[V.RUN, "0"]],
                                 "work/child.json": [[V.RUN, f"0>{exe}#0|-c#0"], [V.RUN, f"0>{exe}#1|-c#0"]],
                                 "work/out.json": [[V.RUN, f"0>{exe}#2"]]})
        self.assertEqual(V.process_of(f"0>{exe}#2"), "0")
        self.assertEqual(V.process_of(f"0>{exe}#0|-c#0>t#1"), f"0>{exe}#0|-c#0")


if __name__ == "__main__":
    unittest.main()

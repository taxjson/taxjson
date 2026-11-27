"""scripts/style/byte_proof.py (PLAN step 15): the A/B byte proof, on a tiny
synthetic project whose `tjs` is a stand-in (what each command prints in each
pass and writes). An equal pair passes; a byte, the merged order, an exit
code, a file, a mode, a folder fail; recordings of other instants, commands,
environments, isolation, Pythons, packages or tools, or that do not match
their manifest, are refused, and a command that exits 2 without saying so is
refused. The clock stands still and the files are older than it (TJ-4491,
TJ-4495), so the round-2 review's mutants and Fable's (a date a day off or in
UTC, a time from the clock, the default piped width, a colour, a mode, an
empty folder, a .diag line, a January to April branch) each differ where they
should, through a real child at the proof's clocks; and so do the mutants of
the passes Fable's second review asked for (TJ-4579): a terminal under the
width's floor and over its cap, COLUMNS ignored, a stream's width or a
prompt decided by the other stream, NO_COLOR ignored, an answer typed at a
prompt, a message of an edited project; and those of Fable's third review
(TJ-4624): TAXJSON_WIDTH's floor and a non-number, COLUMNS read when piped,
the encoding's fallback, the exit when stdout's reader went away. Every
child runs in a network namespace under a shim that refuses sockets: a
child that reaches out fails, and says so. The reach gate refuses a
comparison unless every src line the diff changes was reached as it needs
on its side (TJ-4624, TJ-4629 to TJ-4636: BASE's deleted lines in BASE's
recording, branches both ways, two iterations of a loop, the tries around
a statement, its text shown, every byte; or the line is listed by its
number and text): Fable's fourth review's cases and handler mutants are
refused, the pure move and the sound changes pass. Tracing moves no
recorded byte. One tree recorded twice on real commands is equal, with a
warning. Where no network namespace can be made (`unshare`),
the tests that run a child are skipped, saying why, and a recording is
refused."""
import hashlib
import ast
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _paths

TOOL = "scripts/style/byte_proof.py"


def _load():
    spec = importlib.util.spec_from_file_location("byte_proof_under_test", _paths.REPO / TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BP = _load()
COMMANDS = {"b": ["sum", "sum --json"]}
AT, MARCH = BP.INSTANTS
LABEL, MARCH_LABEL = BP.instant_label(AT), BP.instant_label(MARCH)
EVERY = [p.name for p in BP.PASSES if p.on == "every"]
WIDTH = [p.name for p in BP.PASSES if p.on == "width"]
ALL = {p.name for p in BP.PASSES}
TERMINAL = {p.name for p in BP.PASSES if p.columns}
PIPED = [p.name for p in BP.PASSES if p.streams == "piped"]
# The passes a command runs that prints on stdout and whose bytes move with
# the width, colouring nothing (`fake()`): every pass but `ascii` and `nocolor`.
MOVING = EVERY + ["gone"] + WIDTH + ["pipedenv"]
PKGS = {"list": ["made-up==1.0"], "digest": "0" * 16}
# Why the tests that run a child cannot run here, or "" (CI without
# `unshare`): skipped with this reason, never passed in silence.
NO_NAMESPACE = BP.isolation_problem()
needs_namespace = unittest.skipIf(NO_NAMESPACE, f"no network namespace here: {NO_NAMESPACE}")


# A hang fixture's processes carry this in their command line (`_end`
# kills only those).
_HANG_MARK = "byte-proof-test-hang"


def _hanging(kind: str, pgid_file: Path, own_death: bool = True, grandchild: bool = True) -> list:
    """The argv of a child for the hang tests. It writes its process group
    to `pgid_file`, starts a grandchild in that group (its stdio not the
    child's, but in "holds") that sleeps, then: "prompt" asks again after
    each EOF; "sleep" sleeps; "closed" closes its stdio and sleeps; "exits"
    ends, the grandchild left asleep; "holds" ends, the grandchild left
    asleep holding its stdio. Each ends within 30 seconds (SIGALRM: a
    prompt blocks once the queued EOFs are read), and the child
    (`own_death`) asks for SIGKILL when its parent dies (the grandchild
    does not: only the tool kills it): a failed, mutated or killed test
    leaves nothing for long, and `_end` kills what it left."""
    death = "ctypes.CDLL(None).prctl(1, 9, 0, 0, 0)"    # PR_SET_PDEATHSIG, SIGKILL
    lines = ["import ctypes, os, signal, subprocess, sys, time", "signal.alarm(30)", "end = time.monotonic() + 30"]
    lines += [death] if own_death else []
    # Written whole, then renamed into place: a test that sees the file
    # reads its number (a plain open-then-write left it empty for a moment,
    # and a loaded machine's test read '' there: TJ-5335).
    lines.append(f"open({str(pgid_file) + '.part'!r}, 'w').write(str(os.getpgrp()))")
    lines.append(f"os.replace({str(pgid_file) + '.part'!r}, {str(pgid_file)!r})")
    if grandchild:
        sleeper = "import signal, time; signal.alarm(30); time.sleep(30)"
        stdio = "" if kind == "holds" else (", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                                            "stderr=subprocess.DEVNULL")
        lines.append(f"subprocess.Popen([sys.executable, '-c', {sleeper!r}, sys.argv[1]]{stdio})")
    lines.append({"prompt": "while time.monotonic() < end:\n    try:\n        input('? ')\n"
                            "    except EOFError:\n        pass",
                  "sleep": "time.sleep(30)",
                  "closed": "os.close(0)\nos.close(1)\nos.close(2)\ntime.sleep(30)",
                  "exits": "pass", "holds": "print('done', flush=True)"}[kind])
    return [sys.executable, "-P", "-c", "\n".join(lines), _HANG_MARK]


def _group(pgid: int) -> list:
    """The processes of group `pgid` (/proc), zombies left out."""
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                state, _ppid, pgrp = (d / "stat").read_text().rsplit(")", 1)[1].split()[:3]
            except (OSError, ValueError):
                continue
            if state != "Z" and int(pgrp) == pgid:
                out.append(int(d.name))
    return out


def _left(pgid_file: Path) -> list:
    """The processes a hang fixture left, after a moment for the killed
    to end."""
    pgid = int(pgid_file.read_text())
    deadline = time.monotonic() + 10
    while (left := _group(pgid)) and time.monotonic() < deadline:
        time.sleep(0.05)
    return left


def _end(pgid_file: Path) -> None:
    """A test's cleanup, whatever it asserted: every process a hang fixture
    left (in its group, carrying its mark) killed."""
    if not pgid_file.exists():
        return
    for pid in _group(int(pgid_file.read_text())):
        try:
            if _HANG_MARK.encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def crlf(data):
    return data.replace(b"\n", b"\r\n")


def fake(stdout=b"REPORT at {cwd}\n", stderr=b"warning: made up\n", exit_code=0,
         stderr_first=True, writes=True, changed_at=None, moves=True, on_terminal=b""):
    """A stand-in for one `tjs` command: its output names the folder it ran
    in (masked) and, when it `moves` with the width, its width (the pass's
    TAXJSON_WIDTH, else COLUMNS, else the terminal's); `changed_at`
    (TAXJSON_WIDTH, command) prints one byte differently there;
    `on_terminal` is printed on a terminal only."""
    def run(words, cwd, env, how, term=None):
        width = env.get("TAXJSON_WIDTH") or env.get("COLUMNS") or (str(term.columns) if term else None)
        out = stdout.replace(b"{cwd}", str(cwd).encode()) + (f"width {width}\n".encode() if moves else b"")
        if changed_at == (width, " ".join(words)):
            out = out.replace(b"REPORT", b"REPORt")
        if term:
            out += on_terminal
        if writes:
            (cwd / "reports" / "new.txt").write_text(f"{words[0]} {width}\n")
            (cwd / "work" / "x.state").write_text("stage, not compared\n")
        code = exit_code(how) if callable(exit_code) else exit_code
        both = stderr + out if stderr_first else out + stderr
        return {"apart": ({"stdout": out, "stderr": stderr}, code), "merged": ({"merged": both}, code),
                "gone": ({"stderr": stderr}, code), "outpiped": ({"stdout": out, "out": crlf(stderr)}, code),
                "errpiped": ({"stderr": stderr, "out": crlf(out)}, code)}.get(how, ({"out": crlf(both)}, code))
    return run


def kinds(name):
    p = next(x for x in BP.PASSES if x.name == name)
    return {"piped": ("stdout", "stderr", "merged"), "apart": ("stdout", "stderr"), "gone": ("stderr",),
            "tty": ("out",), "outpiped": ("stdout", "out"), "errpiped": ("stderr", "out")}[p.streams]


# A child Python standing for `tjs run`, each mutant (BP_TEST_MUTANT) a
# change the proof must see: round 2's (review1r2/mutP: `day` the radar's
# date a day off, `format` its JSON date as %Y-%d-%m, `utc` a file's time
# in UTC, `newline` a .sum without its final newline) and Fable's of 1c
# (fable_bp/mut: `piped` the default piped width 119, `clock` years' last
# run from the clock, not the file, `utcnow` the run's date in UTC,
# `colour` another colour on a terminal, `chmod` a file's mode, `emptydir`
# a new empty folder, `diag` a detect .diag line), `month` a January to
# April branch (`month <= 2`), `above` a write above the project, `home`
# one into HOME; and those of the passes of TJ-4579 (lib/out's `width` in
# small): `floor` a floor of 30, `cap` a cap of 150, `columns` the
# terminal asked, not COLUMNS, `stream` stderr's width from stdout's,
# `prompt` a prompt asked when stdout is a terminal, `answer` the answer
# read the other way, `stale` an edited project's message, `nocolor`
# NO_COLOR ignored; and those of TJ-4624 (fable_bp3/mut/N): `envfloor`
# TAXJSON_WIDTH's floor 30 (n1), `pipedcols` COLUMNS read when piped (n2),
# `encoding` stdout's fallback backslashreplace (n3), `gone` the exit 1
# when stdout's reader went away (n9), `nan` a TAXJSON_WIDTH that is not a
# number read as 0 (n11).
_CHILD = """
import datetime, json, os, shutil, sys, time
from pathlib import Path
m = os.environ.get("BP_TEST_MUTANT", "")
w = os.environ.get("TAXJSON_WIDTH")
sys.stdout.reconfigure(errors="backslashreplace" if m == "encoding" else "replace")
tty = sys.stdout.isatty()
def width(stream):
    if w:
        try:
            n = int(w)
        except ValueError:
            n = 0 if m == "nan" else -1
        if n == 0:
            return 0
        if n > 0:
            return max(n, 30 if m == "envfloor" else 40)
    if not stream.isatty():
        if m == "pipedcols" and os.environ.get("COLUMNS"):
            return int(os.environ["COLUMNS"])
        return 119 if m == "piped" else 120
    if m == "columns":
        cols = os.get_terminal_size(stream.fileno()).columns
    else:
        cols = shutil.get_terminal_size((120, 24)).columns
    return max(30 if m == "floor" else 40, min(cols, 150 if m == "cap" else 160))
print(f"width {width(sys.stdout)}")
sys.stderr.write(f"stderr width {width(sys.stdout if m == 'stream' else sys.stderr)}\\n")
sys.stderr.flush()
as_of = datetime.date(2024, 11, 26 if m == "day" else 25)
print(f"WASH RADAR, as of {as_of.isoformat()}")
print(json.dumps({"as_of_date": as_of.strftime("%Y-%d-%m" if m == "format" else "%Y-%m-%d")}))
mtime = time.time() if m == "clock" else Path("old.txt").stat().st_mtime
last = (datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).replace(tzinfo=None)
        if m == "utc" else datetime.datetime.fromtimestamp(mtime))
print(f"2024  open   {last:%Y-%m-%d %H:%M}")
print(f"run on {(datetime.datetime.utcnow() if m == 'utcnow' else datetime.datetime.now()).date()}")
if datetime.date.today().month <= (2 if m == "month" else 4):
    print("Filing 2025 now?")
coloured = tty and (m == "nocolor" or not os.environ.get("NO_COLOR"))
green = ("\\033[33m" if m == "colour" else "\\033[32m") if coloured else ""
print(f"{green}TOTAL\\033[0m" if green else "TOTAL")
print("margin \\u2014 3 rows")
if Path("inputs.csv").read_text() != "rows\\n":
    print("The books are OLDER than the inputs" if m == "stale" else "the books are older than the inputs")
if (sys.stdout if m == "prompt" else sys.stdin).isatty():
    try:
        answer = input("Lock 2024? [y/N] ")
    except EOFError:
        answer = ""
    print("locked" if answer.strip().lower() == ("n" if m == "answer" else "y") else "not locked")
else:
    print("not a terminal: not asked")
Path("reports/margin.sum").write_text("SUMMARY\\nthe last line" + ("" if m == "newline" else "\\n"))
Path("reports/run_summary.json").write_text("{}\\n")
if m == "chmod":
    os.chmod("reports/run_summary.json", 0o755)
if m == "emptydir":
    Path("reports/new.d").mkdir()
Path("work/detect.diag").write_text("a -> b [x)\\n" if m == "diag" else "a -> b (x)\\n")
if m == "above":
    Path("../stray.txt").write_text("x\\n")
if m == "home":
    (Path.home() / ".note").write_text("x\\n")
try:
    sys.stdout.flush()
except BrokenPipeError:
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    sys.exit(1 if m == "gone" else 141)
"""


def child(mutant="", code=None):
    """A stand-in `tjs` that is a real child Python with the proof's
    environment, in its network namespace, piped or on its terminal,
    running _CHILD (or `code`)."""
    argv = [sys.executable, "-P", "-c", code or _CHILD]

    def run(words, cwd, env, how, term=None):
        with mock.patch.object(BP, "_program", lambda _words: argv):
            return BP.invoke(words, cwd, {**env, "BP_TEST_MUTANT": mutant}, how, term, timeout=60)
    return run


class _Project(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        self.root = self.tmp / "p"
        (self.root / "reports").mkdir(parents=True)
        (self.root / "work").mkdir()
        (self.root / "reports" / "a.sum").write_text("sum\n")
        (self.root / "old.txt").write_text("old\n")
        (self.root / "inputs.csv").write_text("rows\n")

    def recording(self, name, run, commands=COMMANDS, passes=BP.PASSES, instants=(AT,)):
        dest = self.tmp / name
        dest.mkdir()
        for c in commands["b"]:
            for at in instants:
                BP.record_command(dest / "b" / BP.command_name(c) / BP.instant_label(at), self.root, ".",
                                  c, at, {}, passes, run=run)
        BP.write_proof(dest, commands, passes, instants=instants, pkgs=PKGS)
        return dest

    def compare(self, run_a, run_b, names=("a", "b"), **kw):
        return BP.compare(self.recording(names[0], run_a, **kw), self.recording(names[1], run_b, **kw))

    def differing(self, a, b):
        with mock.patch.object(BP, "MAX_SHOWN", 10 ** 6):
            code, lines = BP.compare(a, b)
        return code, {ln.split(": ")[0] for ln in lines[1:] if not ln.startswith("Warning")}

    def differing_passes(self, a, b):
        """(exit, {COMMAND/INSTANT/PASS}) of the files that differ."""
        code, paths = self.differing(a, b)
        return code, {"/".join(p.split("/")[1:3] + [p.split("/")[3].split(".")[0]]) for p in paths}

    @staticmethod
    def umask():
        old = os.umask(0)
        os.umask(old)
        return old

    def passes_run(self, dest, cmd="sum"):
        return (dest / "b" / cmd / LABEL / "passes").read_text().split()


class TestTheByteProof(_Project):
    def test_an_equal_pair_passes(self):
        code, lines = self.compare(fake(), fake())
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[0], r"^byte proof: equal \(\d+ files")

    def test_what_a_command_records(self):
        dest = self.recording("a", fake())
        cmd = dest / "b" / "sum" / LABEL
        self.assertEqual(self.passes_run(dest), MOVING)
        want = {"passes"}
        for name in MOVING:
            want |= {f"{name}.meta", f"{name}.files/p/reports/new.txt"} | {f"{name}.{k}" for k in kinds(name)}
        self.assertEqual({p.relative_to(cmd).as_posix() for p in cmd.rglob("*") if p.is_file()}, want)
        self.assertEqual((cmd / "w120.stdout").read_bytes(), b"REPORT at <project>\nwidth 120\n")
        self.assertEqual((cmd / "w40.merged").read_bytes(), b"warning: made up\nREPORT at <project>\nwidth 40\n")
        # TAXJSON_WIDTH unset: piped, and on the terminal at its widths and with COLUMNS
        self.assertEqual((cmd / "piped.stdout").read_bytes(), b"REPORT at <project>\nwidth None\n")
        self.assertEqual((cmd / "tty.out").read_bytes(), b"warning: made up\r\nREPORT at <project>\r\nwidth 100\r\n")
        for name, width in (("tty30", BP.NARROW), ("tty200", BP.WIDE), ("columns", BP.COLUMNS_SET)):
            self.assertTrue((cmd / f"{name}.out").read_bytes().endswith(f"width {width}\r\n".encode()), name)
        self.assertEqual(((cmd / "outpiped.stdout").read_bytes(), (cmd / "outpiped.out").read_bytes()),
                         (b"REPORT at <project>\nwidth 100\n", b"warning: made up\r\n"))
        self.assertEqual(((cmd / "errpiped.stderr").read_bytes(), (cmd / "errpiped.out").read_bytes()),
                         (b"warning: made up\n", b"REPORT at <project>\r\nwidth 100\r\n"))
        self.assertEqual((cmd / "w0.meta").read_text(),
                         "exit 0\nadded file 0644 p/reports/new.txt\nadded file 0644 p/work/x.state\n"
                         "exit merged 0\n".replace("0644", f"{0o666 & ~self.umask():04o}"))
        self.assertEqual((cmd / "w160.files/p/reports/new.txt").read_text(), "sum 160\n")
        # TJ-4624: stdout's reader gone (stderr alone), under the floor, not a number, COLUMNS piped
        self.assertEqual(sorted(p.name for p in cmd.glob("gone.*")), ["gone.files", "gone.meta", "gone.stderr"])
        for name, width in (("w30", "30"), ("wnan", BP.NAN_WIDTH), ("pipedenv", str(BP.COLUMNS_SET))):
            self.assertEqual((cmd / f"{name}.stdout").read_bytes(), f"REPORT at <project>\nwidth {width}\n".encode())
        # each pass on a copy: the project itself is as it was
        self.assertFalse((self.root / "reports" / "new.txt").exists())

    def test_the_passes_a_command_runs(self):
        """The width passes on a command whose bytes move with the width
        (at 40 not as at 0, either stream, or the terminal not as at 0),
        and nowhere else; `nocolor` on a command that colours or whose
        terminal output held an escape; with NO_COLOR or COLUMNS set only
        in their passes. Which ran is recorded: a change to it differs."""
        def stderr_at_40(words, cwd, env, how, term=None):
            return fake(moves=False, stderr=b"x\n" if env.get("TAXJSON_WIDTH") == "40" else b"y\n")(
                words, cwd, env, how, term)
        cases = (
            ("still", fake(moves=False), "sum", EVERY + ["gone"]),
            ("no stdout", fake(moves=False, stdout=b""), "sum", EVERY),
            ("past ascii", fake(moves=False, stdout="margin \u2014\n".encode()), "sum", EVERY + ["gone", "ascii"]),
            ("stdout at 40", fake(moves=False, changed_at=("40", "sum")), "sum", MOVING),
            ("stderr at 40", stderr_at_40, "sum", MOVING),
            ("terminal", fake(moves=False, on_terminal=b"more\n"), "sum", MOVING),
            ("escape", fake(moves=False, stdout=b"\x1b[1mREPORT\x1b[0m\n"), "sum",
             EVERY + ["gone", "pipedenv", "nocolor"]),
            ("colours", fake(moves=False), "taxjson-sum-gains x.json", EVERY + ["gone", "pipedenv", "nocolor"]),
        )
        for label, run, cmd, want in cases:
            with self.subTest(label=label):
                dest = self.recording(label, run, {"b": [cmd]})
                self.assertEqual(self.passes_run(dest, BP.command_name(cmd)), want)
        seen = []

        def run(words, cwd, env, how, term=None):
            seen.append((how, env.get("NO_COLOR"), env.get("COLUMNS"), env.get("TAXJSON_WIDTH")))
            return fake()(words, cwd, env, how, term)
        BP.record_command(self.tmp / "d", self.root, ".", "audit", AT, {}, run=run)
        self.assertEqual([s for s in seen if s[1] or s[2]],
                         [("tty", None, "72", None), ("apart", "1", "72", None), ("tty", "1", None, None)])
        self.assertEqual([s for s in seen if s[3] in ("30", BP.NAN_WIDTH)], [("apart", None, None, "30"),
                                                                           ("apart", None, None, BP.NAN_WIDTH)])
        a, b = self.recording("a", fake(moves=False)), self.recording("b", fake(changed_at=("40", "sum")))
        code, differ = self.differing(a, b)
        self.assertEqual(code, 1)
        self.assertIn(f"b/sum/{LABEL}/passes", differ)

    def test_a_one_byte_difference_fails(self):
        code, lines = self.compare(fake(), fake(changed_at=("120", "sum --json")))
        self.assertEqual(code, 1)
        self.assertEqual(lines[1:], [f"b/sum_json/{LABEL}/w120.merged: line 2: b'REPORT at <<project>', "
                                     "now b'REPORt at <<project>'".replace("<<", "<"),
                                     f"b/sum_json/{LABEL}/w120.stdout: line 1: b'REPORT at <project>', "
                                     "now b'REPORt at <project>'"])

    def test_a_merged_order_difference_fails(self):
        """The same stdout and stderr apart, written in another order (on
        the terminal too)."""
        code, lines = self.compare(fake(), fake(stderr_first=False))
        self.assertEqual(code, 1)
        self.assertEqual({ln.split(": ")[0] for ln in lines[1:]},
                         {f"b/{c}/{LABEL}/{p}.merged" for c in ("sum", "sum_json") for p in PIPED}
                         | {f"b/{c}/{LABEL}/{p}.out" for c in ("sum", "sum_json")
                            for p in ("tty", "tty30", "tty200", "columns")})

    def test_an_exit_code_difference_fails(self):
        code, lines = self.compare(fake(), fake(exit_code=1))
        self.assertEqual(code, 1)
        self.assertIn(f"b/sum/{LABEL}/w0.meta: line 1: b'exit 0', now b'exit 1'", lines)

    def test_a_file_written_or_not_fails(self):
        code, lines = self.compare(fake(), fake(writes=False))
        self.assertEqual(code, 1)
        self.assertIn(f"b/sum/{LABEL}/w0.files/p/reports/new.txt: only in " + str(self.tmp / "a"), lines)

    def test_a_usage_error_is_refused_unless_the_line_says_so(self):
        """`sum --no-such-flag` exits 2 in both recordings: equal, unless
        refused (Fable P2). A line that states its exit is checked in every
        pass, the terminal's too."""
        with self.assertRaisesRegex(SystemExit, "usage error"):
            self.recording("a", fake(exit_code=2), {"b": ["sum --no-such-flag"]})
        with self.assertRaisesRegex(SystemExit, "tty tty exited 2"):
            self.recording("b", fake(exit_code=lambda how: 2 if how == "tty" else 0), {"b": ["sum"]})
        with self.assertRaisesRegex(SystemExit, "outpiped outpiped exited 2"):
            self.recording("b2", fake(exit_code=lambda how: 2 if how == "outpiped" else 0), {"b": ["sum"]})
        self.recording("c", fake(exit_code=2), {"b": ["sum --no-such-flag # exit 2"]})
        with self.assertRaisesRegex(SystemExit, "states exit 1"):
            self.recording("d", fake(exit_code=0), {"b": ["sum # exit 1"]})
        with self.assertRaisesRegex(SystemExit, "states exit 1"):
            self.recording("e", fake(exit_code=lambda how: 0 if how == "merged" else 1), {"b": ["sum # exit 1"]})
        self.assertEqual(BP.parse_command("../2025: handoff --json  # exit 2"),
                         ("../2025", "handoff --json", 2, None, (), (), ()))
        # stdout's reader gone: its exit is the pipe's, never checked (TJ-4624)
        self.recording("f", fake(exit_code=lambda how: 2 if how == "gone" else 0), {"b": ["sum"]})
        self.assertEqual(BP.command_name("sum --no-such-flag # exit 2"), "sum_no-such-flag")

    def test_a_line_states_its_stdin_and_its_edits(self):
        """`# stdin: TEXT` is typed at the terminal (`\\n` a line break),
        then EOF; the line runs every pass, its stdin /dev/null on a pipe,
        and its exit is checked where stdin is the terminal (TJ-4624: a
        refusal off a terminal is recorded); `# edit: OP PATH [TEXT]`
        changes the copy before each pass, inside it only, and no path in
        an appended TEXT leaves it."""
        line = "close-year # exit 1 # stdin: y\\nnote # edit: append inputs.csv more rows # edit: remove old.txt"
        self.assertEqual(BP.parse_command(line), (".", "close-year", 1, "y\\nnote",
                                                  (("append", "inputs.csv", "more rows"), ("remove", "old.txt", "")),
                                                  (), ()))
        self.assertEqual(BP.command(*BP.parse_command(line)), line)
        self.assertEqual(BP.command_name(line), "close-year~stdin-y_nnote~append-inputs.csv-more_rows~remove-old.txt")
        self.assertEqual(BP.stdin_bytes("y\\nnote"), b"y\nnote\n")
        for bad in ("sum # stdin: y # stdin: n", "sum # edit: chmod x", "sum # edit: touch x y", "sum # exit",
                    "sum # exit 1 # exit 2", "sum # edit: remove"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                BP.parse_command(bad)
        self.assertEqual(BP.parse_command("sum # not a note"), (".", "sum # not a note", None, None, (), (), ()))
        seen = []

        def run(words, cwd, env, how, term=None):
            seen.append((how, term.stdin if term else None, (cwd / "inputs.csv").read_text(),
                         (cwd / "old.txt").exists(), (cwd / "inputs.csv").stat().st_mtime_ns))
            return fake()(words, cwd, env, how, term)
        BP.record_command(self.tmp / "d", self.root, ".", line.replace("# exit 1 ", ""), AT, {}, run=run)
        ran = [p for name in MOVING for p in BP.PASSES if p.name == name]
        self.assertEqual([s[0] for s in seen], [how for p in ran for how in BP._how(p)])
        dated = (BP.epoch(AT) - BP.EDIT_AGE) * 1_000_000_000
        self.assertEqual({s[1:] for s in seen}, {(b"y\nnote\n", "rows\nmore rows\n", False, dated),
                                                 (None, "rows\nmore rows\n", False, dated)})
        self.assertEqual({s[1] for s, how in zip(seen, [h for p in ran for h in BP._how(p)])
                          if how in ("apart", "merged", "gone")}, {None})
        self.assertEqual((self.tmp / "d" / "passes").read_text().split(), MOVING)
        # a refusal off a terminal (exit 2 on a pipe) is recorded, not refused; on a terminal it is
        off = fake(exit_code=lambda how: 0 if how in ("tty", "outpiped", "errpiped") else 2)
        BP.record_command(self.tmp / "o", self.root, ".", "close-year # stdin: y", AT, {}, run=off)
        self.assertEqual((self.tmp / "o" / "w0.meta").read_text().splitlines()[0], "exit 2")
        with self.assertRaisesRegex(SystemExit, "w0 apart exited 2"):
            BP.record_command(self.tmp / "o2", self.root, ".", "close-year", AT, {}, run=off)
        with self.assertRaisesRegex(SystemExit, "tty tty exited 2"):
            BP.record_command(self.tmp / "o3", self.root, ".", "close-year # stdin: y", AT, {},
                              run=fake(exit_code=lambda how: 2 if how == "tty" else 0))
        BP.record_command(self.tmp / "t", self.root, ".", "sum # edit: touch inputs.csv", AT, {}, run=run)
        self.assertEqual(seen[-1][2:], ("rows\n", True, dated))
        # refused before anything runs: outside the copy, through a link out, a folder, nothing there
        (self.root / "out").symlink_to(self.tmp)
        nothing = mock.Mock(side_effect=AssertionError("ran"))
        for edit, why in (("append ../x y", "above the copy"), ("append /x y", "outside the copy"),
                          ("touch ~/x", "outside the copy"), ("append out/x y", "resolves outside the copy"),
                          ("remove out", "resolves outside the copy"), ("touch reports", "a folder"),
                          ("remove missing.txt", "no such file"),
                          # P4 (TJ-4624): a path in an appended text
                          ('append taxjson.toml inputs_dir = "/etc"', "outside the copy"),
                          ("append taxjson.toml inputs_dir = '../..'", "above the copy"),
                          ("append work/x.json {\"in\": \"../../../y\"}", "above the copy"),
                          ("append x.toml dir = ['out/z']", "resolves outside the copy"),
                          ("append x.toml dir=~/z", "outside the copy")):
            with self.subTest(edit=edit), self.assertRaisesRegex(SystemExit, f"refused.*{why}"):
                BP.record_command(self.tmp / "e", self.root, ".", f"sum # edit: {edit}", AT, {}, run=nothing)
        self.assertTrue((self.root / "out").is_symlink())
        nothing.assert_not_called()

    def test_a_line_runs_its_before_commands_first(self):
        """`# before: ARGV` runs on the copy after the line's edits, piped at
        width 0, unrecorded, in each pass, before the command; it must exit
        0; the copy is dated again after it; the guard reads it as a line."""
        line = "sum # edit: append inputs.csv more # before: run --no-input # before: -C . years"
        parsed = BP.parse_command(line)
        self.assertEqual(parsed.before, ("run --no-input", "-C . years"))
        self.assertEqual(BP.command(*parsed), line)
        self.assertTrue(BP.command_name(line).endswith("~before-run_--no-input~before--C_._years"))
        seen = []

        def run(words, cwd, env, how, term=None):
            seen.append((words, how, env.get("TAXJSON_WIDTH"), (cwd / "inputs.csv").read_text(),
                         (cwd / "inputs.csv").stat().st_mtime_ns))
            if words == ["run", "--no-input"]:
                (cwd / "built.txt").write_text("books\n")
                return {"stdout": b"ran\n", "stderr": b""}, 0
            if words[-1] == "years":
                return {"stdout": b"", "stderr": b""}, 0
            return fake()(words, cwd, env, how, term)
        BP.record_command(self.tmp / "d", self.root, ".", line, AT, {}, BP.PASSES[:2], run=run)
        dated, edited = ((BP.epoch(AT) - age) * 1_000_000_000 for age in (BP.FILE_AGE, BP.EDIT_AGE))
        self.assertEqual([(s[0][0], s[1], s[2]) for s in seen],
                         [step for w in ("0", "40") for step in (
                             ("run", "apart", "0"), ("-C", "apart", "0"), ("sum", "apart", w),
                             ("run", "apart", "0"), ("-C", "apart", "0"), ("sum", "merged", w))])
        self.assertEqual({(s[0][0], s[3], s[4]) for s in seen},
                         {("run", "rows\nmore\n", edited), ("-C", "rows\nmore\n", edited),
                          ("sum", "rows\nmore\n", dated)})
        self.assertNotIn("built.txt", (self.tmp / "d" / "w0.meta").read_text())    # before's, not the command's
        self.assertNotIn("ran", (self.tmp / "d" / "w0.stdout").read_text())

        def failing(words, cwd, env, how, term=None):
            return ({"stdout": b"", "stderr": b"Error: no books\n"}, 1) if words[0] == "run" else (
                fake()(words, cwd, env, how, term))
        with self.assertRaisesRegex(SystemExit, r"`# before: run --no-input` exited 1: Error: no books"):
            BP.record_command(self.tmp / "f", self.root, ".", "sum # before: run --no-input", AT, {}, run=failing)
        # an edit after the commands before it (TJ-4624: `compare` against the
        # snapshot a `# before:` saved, the project changed since)
        line = "sum # before: run --no-input # edit: append inputs.csv later"
        parsed = BP.parse_command(line)
        self.assertEqual((parsed.edits, parsed.before, parsed.after),
                         ((), ("run --no-input",), (("append", "inputs.csv", "later"),)))
        self.assertEqual(BP.command(*parsed), line)
        self.assertTrue(BP.command_name(line).endswith("~before-run_--no-input~then-append-inputs.csv-later"))
        seen.clear()
        BP.record_command(self.tmp / "g", self.root, ".", line, AT, {}, BP.PASSES[:1], run=run)
        self.assertEqual({(s[0][0], s[3], s[4]) for s in seen},
                         {("run", "rows\n", dated), ("sum", "rows\nlater\n", edited)})
        with self.assertRaisesRegex(ValueError, "never after an edit made after one"):
            BP.parse_command("sum # before: run # edit: append x y # before: years")
        with self.assertRaisesRegex(SystemExit, "refused.*above the copy"):
            BP.record_command(self.tmp / "h", self.root, ".", "sum # before: run # edit: append ../x y", AT, {},
                              run=mock.Mock(side_effect=AssertionError("ran")))
        nothing = mock.Mock(side_effect=AssertionError("ran"))
        for bad, why in (("sum # edit: touch inputs.csv # before: run", "dated again"),
                         ("sum # before: deploy", "`deploy` is denied"), ("sum # before: run -C/x", "glued -C"),
                         ("sum # before: run ../..", "above the copy"), ("sum # before: fetch --list", "denied")):
            with self.subTest(bad=bad), self.assertRaisesRegex(SystemExit, f"refused.*{why}"):
                BP.record_command(self.tmp / "e", self.root, ".", bad, AT, {}, run=nothing)
        nothing.assert_not_called()

    def test_other_instants_commands_environments_pythons_or_tools_are_refused(self):
        a, b = self.recording("a", fake()), self.recording("b", fake(), {"b": ["sum"]})
        self.assertEqual(BP.compare(a, b)[0], 2)
        b = self.recording("c", fake())
        proof = json.loads((a / BP.PROOF).read_text())
        for key, other in (("instants", [MARCH]), ("python", "cpython 3.13.1"), ("tool", "0" * 16),
                           ("tz", "UTC0"), ("passes", [["w0", "0", "piped", None, [], "every"]]), ("format", 2),
                           ("env", {**proof["env"], "LANG": "C"}), ("file_age", 0), ("edit_age", 0),
                           ("tty_rows", 24), ("commands", {"b": ["sum"]}), ("once", {"b": ["sum"]}),
                           ("isolation", {**proof["isolation"], "namespace": "none"}),
                           ("packages", {"list": ["made-up==1.1"], "digest": "1" * 16}),
                           ("checkout_kind", "no git"), ("tmpdir", [proof["tmpdir"][0] + 1] * 2)):
            with self.subTest(key=key):
                (b / BP.PROOF).write_text(json.dumps({**proof, key: other}))
                code, lines = BP.compare(a, b)
                self.assertEqual(code, 2)
                self.assertIn(f"recordings' {key} differ", lines[0])
        with redirect_stdout(io.StringIO()) as said:
            self.assertEqual(BP.main(["--compare", str(a), str(self.tmp / "none"), "--trees", "x", "y"]), 2)
        self.assertIn("not a recording", said.getvalue())

    def test_a_recording_that_is_not_its_manifest_is_refused(self):
        """One command's folder gone from both, a file changed or added, a
        proof.json with no manifest (Fable P3): refused, not equal."""
        a, b = self.recording("a", fake()), self.recording("b", fake())
        self.assertEqual(BP.compare(a, b)[0], 0)
        for d in (a, b):
            shutil.rmtree(d / "b" / "sum_json")
        code, lines = BP.compare(a, b)
        self.assertEqual(code, 2)
        self.assertIn(f"refused: {a} does not match its proof.json manifest: b/sum/", lines[0].replace("sum_json", "sum"))
        c = self.recording("c", fake())
        (c / "b" / "sum" / LABEL / "w0.stdout").write_bytes(b"other\n")
        self.assertIn("not the file recorded", BP.compare(self.recording("d", fake()), c)[1][0])
        e = self.recording("e", fake())
        (e / "b" / "stray.txt").write_text("x\n")
        self.assertIn("b/stray.txt: not in its manifest", BP.compare(e, c)[1][0])
        bare = [self.tmp / "bare1", self.tmp / "bare2"]
        for d in bare:
            d.mkdir()
            proof = json.loads((self.tmp / "a" / BP.PROOF).read_text())
            del proof["manifest"]
            (d / BP.PROOF).write_text(json.dumps(proof))
        code, lines = BP.compare(*bare)
        self.assertEqual(code, 2)
        self.assertIn("no manifest", lines[0])

    def test_the_day_recorded_is_not_compared(self):
        """The midnight case cannot arise: a recording that ends on another
        day than the other's still ran every child at its instants."""
        a, b = self.recording("a", fake()), self.recording("b", fake())
        proof = json.loads((b / BP.PROOF).read_text())
        (b / BP.PROOF).write_text(json.dumps({**proof, "recorded": "2000-01-02"}))
        self.assertEqual(BP.compare(a, b)[0], 0)

    def test_one_tree_twice_is_a_warning_not_a_pass(self):
        a, b = self.recording("a", fake()), self.recording("b", fake())
        for d in (a, b):
            proof = json.loads((d / BP.PROOF).read_text())
            (d / BP.PROOF).write_text(json.dumps({**proof, "tree": "no git files abc123"}))
        code, lines = BP.compare(a, b)
        self.assertEqual(code, 3)
        self.assertEqual(lines[0].split(" (")[0], "byte proof: equal")
        self.assertTrue(lines[1].startswith("Warning: both recordings are of one tree (no git files abc123)"))
        c = self.recording("c", fake(exit_code=1))
        proof = json.loads((c / BP.PROOF).read_text())
        (c / BP.PROOF).write_text(json.dumps({**proof, "tree": "no git files abc123"}))
        code, lines = BP.compare(a, c)
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("Warning:"))

    def test_only_the_folders_and_a_builds_commit_are_masked(self):
        """Not golden's stamps (TJ-4491): each value they mask is bytes
        here (each block below is one golden masks)."""
        kept = ("V042\nAtaxjson\nD11/18/2026\n", "generated_at = 2026-11-18T12:00:00\n",
                '"generated_at": "2026-11-18T12:00:00"\n', '"as_of_date": "2024-11-25"\n',
                '"closed_at": "2026-11-18T12:00:00"\n', "Generated: 2026-11-18\n",
                "# generated 2026-11-18 12:00\n", "WASH RADAR — margin, as of 2024-11-25\n",
                " Valet for 12 dates\n", '"reports/margin.sum": "' + "ab" * 32 + '"\n',
                "id=0123456789abcdef\n",
                '"taxjson": {"modified": false, "release": "v0.41.0", "version": "0.41.0"}\n')
        for block in kept:
            with self.subTest(block=block):
                self.assertNotEqual(BP._golden._masked(block), block)
                self.assertEqual(BP.masked(block.encode(), {}), block.encode())
        self.assertEqual(
            BP.masked(b"\xff at /x/p/reports \t\n"
                      b'"taxjson": {\n  "commit": "81c512b",\n  "modified": false\n}\n'
                      b"closed with taxjson 0.41.0 (commit 81c512bd, modified)\n"
                      b'"commit": "81c512b" elsewhere\n',
                      BP.folder_labels(Path("/x/p"), "<project>")),
            b"\xff at <project>/reports \t\n"
            b'"taxjson": {\n  "commit": "<commit:7>",\n  "modified": false\n}\n'
            b"closed with taxjson 0.41.0 (commit <commit:8>, modified)\n"
            b'"commit": "81c512b" elsewhere\n')

    def test_the_package_fingerprint_is_labelled_and_nothing_beside_it(self):
        """The code every work/ record names (TJ-4495, the sanity review):
        a comment moves it; labelled, that value alone, so another digest
        and a change beside it still show."""
        fp = BP.code_fingerprint()
        self.assertRegex(fp, r"^[0-9a-f]{64}$")
        labels = BP.book_labels(self.tmp / "run", self.tmp / "views", self.tmp / "run" / "pristine")
        self.assertEqual(labels[fp], "<code-fingerprint>")
        self.assertEqual(labels[str(self.tmp / "run" / "p")], "<run>")
        self.assertEqual(labels[str(BP.shim_dir())], "<shim>")
        rec = '{"code": "%s", "out": "%s", "n": %d}'
        self.assertEqual(BP.masked((rec % (fp, "ab" * 32, 1)).encode(), labels),
                         (rec % ("<code-fingerprint>", "ab" * 32, 1)).encode())
        self.assertNotEqual(BP.masked((rec % (fp, "ab" * 32, 1)).encode(), labels),
                            BP.masked((rec % (fp, "ab" * 32, 2)).encode(), labels))
        self.assertNotEqual(BP.masked((rec % (fp, "ab" * 32, 1)).encode(), labels),
                            BP.masked((rec % (fp, "ac" * 32, 1)).encode(), labels))
        self.assertEqual(BP.masked(fp[:12].encode(), labels), fp[:12].encode())

    def test_two_texts_never_mask_to_one(self):
        """Fable P3: the copy's ../2025 and the folder above's 2025, a
        `<project>` a command printed and the copy's path, a short commit
        and a full one."""
        labels = {**BP.folder_labels(Path("/x"), "<top>"), **BP.folder_labels(Path("/x/p"), "<project>")}
        pairs = ((b"/x/p/../2025", b"/x/2025"), (b"<project>/a", b"/x/p/a"), (b"<<project>", b"<" + b"/x/p"),
                 (b'"taxjson": {"commit": "81c512b"}', b'"taxjson": {"commit": "' + b"81c512b" * 5 + b'd"}'))
        for x, y in pairs:
            with self.subTest(x=x, y=y):
                self.assertNotEqual(BP.masked(x, labels), BP.masked(y, labels))
        self.assertEqual(BP.masked(b"a < b <= c", {}), b"a << b <<= c")

    def test_every_child_reads_one_instant(self):
        """tests/_clockshift's TAXJSON_TEST_CLOCK_AT, in the zone TZ
        whatever the caller's: the same time before and after a second's
        sleep, and in two children; 21:30 local is the next day in UTC.
        The network shim comes first and loads the clock's after it."""
        code = ("import datetime, time; print(datetime.datetime.now(), datetime.date.today(), "
                "datetime.datetime.utcnow(), time.time(), time.time_ns() // 10 ** 9, time.ctime(), "
                "time.strftime('%Y-%m-%d %H:%M:%S %Z'), datetime.datetime.now(datetime.timezone.utc))")
        with mock.patch.dict(os.environ, {"TZ": "UTC0", "TAXJSON_TEST_CLOCK_DAYS": "3"}):
            env = BP.child_env(AT, self.tmp / "home", self.tmp)
        self.assertEqual(env["TZ"], BP.TZ)
        self.assertNotIn("TAXJSON_TEST_CLOCK_DAYS", env)
        shown = []
        for _ in range(2):
            shown.append(subprocess.run([sys.executable, "-P", "-c", f"{code}; time.sleep(1.1); {code}"],
                                        env=env, capture_output=True, text=True, check=True).stdout)
        self.assertEqual(shown[0], shown[1])
        at = BP.epoch(AT)
        when = (f"2026-11-18 21:30:00 2026-11-18 2026-11-19 02:30:00 {at}.0 {at} Wed Nov 18 21:30:00 2026 "
                "2026-11-18 21:30:00 EST 2026-11-19 02:30:00+00:00\n")
        self.assertEqual(shown[0], when * 2)
        self.assertEqual(BP.epoch("2026-11-18T21:30:00-05:00"), BP.epoch("2026-11-19T02:30:00+00:00"))
        self.assertEqual(BP.instant_label(MARCH), "20260302T2130")
        with self.assertRaisesRegex(ValueError, "offset"):
            BP.epoch("2026-11-18T12:00:00")

    def test_the_environment_is_the_tools_own(self):
        """Nothing inherited (Fable P4): NO_COLOR, COLUMNS, a TAXJSON_
        setting, anything else the caller has; TAXJSON_WIDTH, NO_COLOR and
        COLUMNS only when the pass sets them; the shape in proof.json."""
        caller = {"NO_COLOR": "1", "COLUMNS": "33", "LINES": "9", "TAXJSON_WIDTH": "7",
                  "TAXJSON_LOCAL_TZ": "Asia/Tokyo", "PYTHONHASHSEED": "1", "FOO": "bar", "LANG": "fr_FR"}
        with mock.patch.dict(os.environ, caller):
            env = BP.child_env(AT, self.tmp / "home", self.tmp / "t")
            piped = BP.child_env(AT, self.tmp / "home", self.tmp / "t", 40)
            nocolor = BP.child_env(AT, self.tmp / "home", self.tmp / "t", None, (("NO_COLOR", "1"),))
        for k in caller:
            if k != "LANG":
                self.assertNotIn(k, env)
        self.assertEqual((env["LANG"], piped["TAXJSON_WIDTH"], nocolor["NO_COLOR"]), ("C.UTF-8", "40", "1"))
        self.assertEqual((env["HOME"], env["TMPDIR"]), (str(self.tmp / "home"), str(self.tmp / "t")))
        self.assertTrue(all(env[k] == BP._CLOSED_PORT for k in ("http_proxy", "HTTPS_PROXY", "all_proxy")))
        shape = json.loads((self.recording("a", fake()) / BP.PROOF).read_text())["env"]
        self.assertEqual(shape, BP.env_shape())
        self.assertEqual((shape["HOME"], shape["TAXJSON_WIDTH"], shape["PYTHONPATH"]),
                         ("<home>", "<width>", "<shim>:<tree>/tests/_clockshift:<tree>/src"))
        self.assertEqual({tuple(p.env) for p in BP.PASSES} - {()},
                         {(("COLUMNS", str(BP.COLUMNS_SET)),), (("NO_COLOR", "1"),), (("PYTHONIOENCODING", "ascii"),),
                          (("COLUMNS", str(BP.COLUMNS_SET)), ("NO_COLOR", "1"))})
        # COLUMNS and NO_COLOR set on a pipe too (TJ-4624)
        self.assertEqual([p.streams for p in BP.PASSES if dict(p.env).keys() >= {"COLUMNS", "NO_COLOR"}], ["apart"])

    def test_the_terminal_widths_are_past_the_floor_and_the_cap(self):
        """TJ-4579: lib/out clamps a terminal's width to [MIN_WIDTH,
        MAX_WIDTH]; NARROW is under the floor, WIDE over the cap, COLUMNS_SET
        neither and not the pty's."""
        from taxjson.lib import out
        self.assertLess(BP.NARROW, out.MIN_WIDTH)
        self.assertGreater(BP.WIDE, out.MAX_WIDTH)
        self.assertTrue(out.MIN_WIDTH < BP.COLUMNS_SET < out.MAX_WIDTH and BP.COLUMNS_SET != BP.TTY_COLUMNS)
        self.assertEqual(sorted(int(p.width) for p in BP.PASSES if p.width and p.width.isdigit()),
                         [0, 30, 40, 120, 160])
        self.assertEqual(BP.WIDTHS, (0, 30, 40, 120, 160))
        # TJ-4624: TAXJSON_WIDTH under the floor (clamped) and not a number (ignored)
        self.assertLess(BP.NARROW, out.MIN_WIDTH)
        self.assertEqual([p.width for p in BP.PASSES if p.width and not p.width.isdigit()], [BP.NAN_WIDTH])
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(BP.NARROW)}):
            self.assertEqual(out.width(io.StringIO()), out.MIN_WIDTH)
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": BP.NAN_WIDTH}):
            self.assertEqual(out.width(io.StringIO()), out.WIDTH)

    def test_every_file_of_a_copy_is_older_than_the_instant(self):
        seen = []

        def run(words, cwd, env, how, term=None):
            seen.extend({p.stat().st_mtime_ns for p in [cwd.parent, *cwd.parent.rglob("*")]})
            return {"stdout": b"", "stderr": b""}, 0
        BP.record_command(self.tmp / "d", self.root, ".", "sum", AT, {}, BP.PASSES[:1], run=run)
        self.assertEqual(set(seen), {(BP.epoch(AT) - BP.FILE_AGE) * 1_000_000_000})
        self.assertGreaterEqual(BP.FILE_AGE, 86400)
        self.assertLess(BP.EDIT_AGE, BP.FILE_AGE)

    def test_each_home_holds_rates_to_a_week_past_the_instant(self):
        """Not past today (TJ-4495: in 1c every child inherited a rate cache
        dated from the day it ran, a recording that moved overnight)."""
        for at, last in ((AT, "2026-11-25"), (MARCH, "2026-03-09")):
            with self.subTest(at=at):
                home = self.tmp / f"home-{last}"
                BP.make_home(home, at)
                cache = json.loads((home / ".currency_price_cache.json").read_text())
                self.assertEqual({spans[-1][1] for key, spans in cache["_coverage"].items()
                                  if key.startswith("boc:")}, {last})
                self.assertTrue((home / ".config").is_dir() and (home / ".local" / "share").is_dir())

    def test_writes_above_the_copy_and_into_home_are_recorded(self):
        def run(words, cwd, env, how, term=None):
            (cwd.parent / "2023").mkdir()
            (Path(env["HOME"]) / ".note").write_text("x\n")
            os.chmod(cwd / "reports" / "a.sum", 0o640)
            return {"stdout": b"", "stderr": b""}, 0
        BP.record_command(self.tmp / "d", self.root, ".", "sum", AT, {}, BP.PASSES[:1], run=run)
        meta = (self.tmp / "d" / "w0.meta").read_text().splitlines()
        self.assertIn("added dir 0755 2023".replace("0755", f"{0o777 & ~self.umask():04o}"), meta)
        self.assertTrue(any(ln.startswith("added file ") and ln.endswith(" home/.note") for ln in meta), meta)
        self.assertIn("mode 0644 to 0640 p/reports/a.sum".replace("0644", f"{0o666 & ~self.umask():04o}"), meta)
        self.assertEqual((self.tmp / "d" / "w0.files" / "home" / ".note").read_text(), "x\n")

    def test_the_folders_are_labelled_apart(self):
        """The copy `<project>`, the folder above it `<top>`, HOME, TMPDIR."""
        def run(words, cwd, env, how, term=None):
            return {"stdout": (f"{cwd}|{cwd.parent}/2023|{os.path.realpath(cwd.parent)}|{env['HOME']}|"
                               f"{env['TMPDIR']}\n").encode(), "stderr": b""}, 0
        BP.record_command(self.tmp / "d", self.root, ".", "align", AT, {}, BP.PASSES[:1], run=run)
        self.assertEqual((self.tmp / "d" / "w0.stdout").read_text(), "<project>|<top>/2023|<top>|<home>|<tmp>\n")

    @needs_namespace
    def test_the_mutants_differ_where_they_should(self):
        """Through a real child at the proof's instants, piped, on a
        terminal and with one stream piped: each mutant a difference in each
        pass, command and instant it can show in, and nowhere else; the
        unmutated pairs equal. Those of TJ-4579 differ only through the
        passes and lines it added (a narrow and a wide terminal, COLUMNS,
        a stream piped alone, NO_COLOR, a stated stdin, an edit), and those
        of TJ-4624 only through its passes (TAXJSON_WIDTH 30 and not a
        number, COLUMNS on a pipe, an ASCII stdout, stdout's reader gone);
        a mutant of stdout alone never shows where stdout's reader went
        away (that pass records stderr, the exit and the files)."""
        both = (LABEL, MARCH_LABEL)
        old = {"commands": {"b": ["run"]}, "instants": (AT, MARCH)}
        lines = ["run", "run # stdin: y", "run # edit: append inputs.csv more"]
        new = {"commands": {"b": lines}, "instants": (AT,)}
        names = [BP.command_name(c) for c in lines]

        def where(passes, cmds=("run",), at=both):
            return {f"{c}/{i}/{p}" for c in cmds for i in at for p in passes}
        terminal = TERMINAL | {"nocolor"}
        printed = ALL - {"gone"}
        want = {
            "day": (old, where(printed)), "format": (old, where(printed)), "utc": (old, where(printed)),
            "clock": (old, where(printed)), "utcnow": (old, where(printed)),
            "piped": (old, where({"piped", "outpiped", "errpiped", "gone", "ascii", "pipedenv", "wnan"})),
            "colour": (old, where({"tty", "tty30", "tty200", "columns", "errpiped"})),
            "month": (old, where(printed, at=(MARCH_LABEL,))),
            "newline": (old, where(ALL)), "diag": (old, where(ALL)), "chmod": (old, where(ALL)),
            "emptydir": (old, where(ALL)), "above": (old, where(ALL)), "home": (old, where(ALL)),
            "floor": (new, where({"tty30"}, names, (LABEL,))),
            "cap": (new, where({"tty200"}, names, (LABEL,))),
            "columns": (new, where({"columns", "outpiped"}, names, (LABEL,))),
            "stream": (new, where({"errpiped"}, names, (LABEL,))),
            "prompt": (new, where({"outpiped"}, names, (LABEL,))),
            "nocolor": (new, where({"nocolor"}, names, (LABEL,))),
            "answer": (new, where(terminal, names[1:2], (LABEL,))),
            "stale": (new, where(printed, names[2:], (LABEL,))),
            "envfloor": (new, where({"w30"}, names, (LABEL,))),
            "pipedcols": (new, where({"pipedenv"}, names, (LABEL,))),
            "encoding": (new, where({"ascii"}, names, (LABEL,))),
            "gone": (new, where({"gone"}, names, (LABEL,))),
            "nan": (new, where({"wnan"}, names, (LABEL,))),
        }
        bases = {}
        for key, kw in (("old", old), ("new", new)):
            bases[key] = self.recording(f"base_{key}", child(), **kw)
            self.assertEqual(BP.compare(bases[key], self.recording(f"again_{key}", child(), **kw))[0], 0)

        def one(mutant):
            return self.recording(f"m_{mutant}", child(mutant), **want[mutant][0])
        # child()'s run patches BP._program around each invocation; from three
        # threads those patches interleave, and the last to leave would put
        # back another thread's stand-in for good (TJ-4870). One patch round
        # the pool restores the real one whatever order they leave in.
        argv = [sys.executable, "-P", "-c", _CHILD]
        with mock.patch.object(BP, "_program", lambda _words: argv), ThreadPoolExecutor(max_workers=3) as pool:
            made = dict(zip(want, pool.map(one, want)))
        self.assertEqual(BP._program.__name__, "_program")
        for mutant, dest in made.items():      # compared here: MAX_SHOWN is patched per compare
            code, differ = self.differing_passes(bases["old" if want[mutant][0] is old else "new"], dest)
            with self.subTest(mutant=mutant):
                self.assertEqual(code, 1)
                self.assertEqual(differ, want[mutant][1])
        run = self.tmp / "base_new" / "b"
        out = (self.tmp / "base_old" / "b" / "run" / LABEL / "w0.stdout").read_text()
        self.assertIn("2024  open   2026-11-17 09:30\nrun on 2026-11-18\n", out)
        self.assertNotIn("Filing", out)
        self.assertIn("Filing 2025 now?", (self.tmp / "base_old" / "b" / "run" / MARCH_LABEL / "w0.stdout").read_text())
        tty = (run / "run" / LABEL / "tty.out").read_bytes()
        self.assertTrue(tty.startswith(b"width 100\r\nstderr width 100\r\n"), tty[:40])
        self.assertIn(b"\x1b[32mTOTAL\x1b[0m\r\n", tty)
        self.assertIn(b"\r\nTOTAL\r\n", (run / "run" / LABEL / "nocolor.out").read_bytes())
        self.assertIn(b"Lock 2024? [y/N] not locked\r\n", tty)
        self.assertIn(b"Lock 2024? [y/N] locked\r\n", (run / names[1] / LABEL / "tty.out").read_bytes())
        self.assertIn(b"the books are older than the inputs\n", (run / names[2] / LABEL / "w0.stdout").read_bytes())
        for name, said in (("tty30", b"width 40\r\n"), ("tty200", b"width 160\r\n"), ("columns", b"width 72\r\n")):
            self.assertTrue((run / "run" / LABEL / f"{name}.out").read_bytes().startswith(said), name)
        self.assertEqual((run / "run" / LABEL / "outpiped.stdout").read_bytes().split(b"\n")[0], b"width 120")
        # stdin and stdout on the terminal: input() writes its prompt to stderr, here piped
        self.assertEqual((run / "run" / LABEL / "errpiped.stderr").read_bytes(),
                         b"stderr width 120\nLock 2024? [y/N] ")
        self.assertIn(b"width 120\n", (self.tmp / "base_old" / "b" / "run" / LABEL / "piped.stdout").read_bytes())
        at = run / "run" / LABEL
        self.assertEqual((at / "gone.meta").read_text().splitlines()[0], "exit 141")
        self.assertEqual((at / "gone.stderr").read_bytes(), b"stderr width 120\n")
        self.assertIn(b"margin ? 3 rows\n", (at / "ascii.stdout").read_bytes())
        self.assertIn("margin \u2014 3 rows\n".encode(), (at / "w0.stdout").read_bytes())
        for name, said in (("w30", b"width 40\n"), ("wnan", b"width 120\n"), ("pipedenv", b"width 120\n")):
            self.assertTrue((at / f"{name}.stdout").read_bytes().startswith(said), name)

    @needs_namespace
    def test_a_terminal_answers_a_prompt_and_then_eof_and_never_hangs(self):
        prompt = ("import os, sys\nprint(sys.stdin.isatty(), sys.stdout.isatty(), sys.stderr.isatty(), "
                  "os.get_terminal_size(0))\nsys.stdout.flush()\nprint('to stderr', file=sys.stderr)\n"
                  "sys.stderr.flush()\nfor _ in range(2):\n    try:\n        print('got', input('Lock 2024 anyway? [y/N] '))\n"
                  "    except EOFError:\n        print('EOF')\nprint(sys.stdin.read() == '')\n")
        env = BP.child_env(AT, self.tmp, self.tmp)
        with mock.patch.object(BP, "_program", lambda _w: [sys.executable, "-P", "-c", prompt]):
            outs, code = BP.invoke_tty(["x"], self.tmp, env, "tty", BP.Terminal(stdin=b"y\n"), timeout=60)
            self.assertEqual((outs, code), ({"out": b"True True True os.terminal_size(columns=100, lines=40)\r\n"
                                                    b"to stderr\r\nLock 2024 anyway? [y/N] got y\r\n"
                                                    b"Lock 2024 anyway? [y/N] EOF\r\nTrue\r\n"}, 0))
            outs, code = BP.invoke(["x"], self.tmp, env, "outpiped", BP.Terminal(columns=30), timeout=60)
            self.assertEqual(outs, {"stdout": b"True False True os.terminal_size(columns=30, lines=40)\n"
                                              b"Lock 2024 anyway? [y/N] EOF\nLock 2024 anyway? [y/N] EOF\nTrue\n",
                                    "out": b"to stderr\r\n"})
            outs, code = BP.invoke(["x"], self.tmp, env, "errpiped", BP.Terminal(), timeout=60)
            # stdin and stdout on the terminal: Python's input() writes its prompt to stderr
            self.assertEqual(outs["stderr"], b"to stderr\nLock 2024 anyway? [y/N] Lock 2024 anyway? [y/N] ")
            self.assertTrue(outs["out"].startswith(b"True True False "), outs)

    @needs_namespace
    def test_a_refused_hang_leaves_no_process_behind(self):
        """A command refused for running too long (on the terminal or piped:
        a prompt asked again after each EOF, a sleep, its stdio closed and
        then a sleep) has its whole process group killed, the grandchild it
        started too, and is reaped; one that exits leaving a process in its
        group has that killed when it exits, and its exit is not held up by
        that process holding its stdout or the terminal."""
        env = BP.child_env(AT, self.tmp, self.tmp)
        cases = (("prompt", "tty", 5, "still running after 5s on the terminal"),
                 ("prompt", "errpiped", 5, "still running after 5s on the terminal"),
                 ("sleep", "apart", 5, "still running after 5s, piped"),
                 ("sleep", "merged", 5, "still running after 5s, piped"),
                 ("closed", "tty", 5, "still running after 5s on the terminal"),
                 ("closed", "apart", 5, "still running after 5s, piped"),
                 ("exits", "tty", 60, ""), ("exits", "apart", 60, ""),
                 ("holds", "tty", 60, ""), ("holds", "merged", 60, ""))
        for i, (kind, how, timeout, refused) in enumerate(cases):
            with self.subTest(kind=kind, how=how):
                mark = self.tmp / f"pgid{i}"
                self.addCleanup(_end, mark)
                with mock.patch.object(BP, "_program", lambda _w, k=kind, m=mark: _hanging(k, m)):
                    start = time.monotonic()
                    if refused:            # killed then, not waited for (it ends by itself at 30 s)
                        with self.assertRaisesRegex(SystemExit, refused):
                            BP.invoke(["x"], self.tmp, env, how, timeout=timeout)
                        self.assertLess(time.monotonic() - start, timeout + 10)
                    else:
                        self.assertEqual(BP.invoke(["x"], self.tmp, env, how, timeout=timeout)[1], 0)
                        self.assertLess(time.monotonic() - start, timeout / 2)
                self.assertTrue(mark.exists(), "the fixture never started (a machine too slow for its timeout)")
                self.assertEqual(_left(mark), [])

    @needs_namespace
    def test_a_recording_killed_leaves_no_command_running(self):
        """A child asks for SIGKILL when the thread that started it dies
        (setpriv --pdeathsig, through unshare): the tool killed while a
        command runs, the command dies with it."""
        mark = self.tmp / "pgid"
        self.addCleanup(_end, mark)
        argv = _hanging("sleep", mark, own_death=False, grandchild=False)
        tool = (f"import importlib.util\nfrom pathlib import Path\n"
                f"spec = importlib.util.spec_from_file_location('bp', {str(_paths.REPO / TOOL)!r})\n"
                "bp = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(bp)\n"
                f"bp._program = lambda _w: {argv!r}\nhere = Path({str(self.tmp)!r})\n"
                f"bp.invoke(['x'], here, bp.child_env({AT!r}, here, here), 'apart', timeout=60)\n")
        p = subprocess.Popen([sys.executable, "-P", "-c", tool])
        try:
            deadline = time.monotonic() + 60
            while not mark.exists() and p.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(mark.exists(), "the command never started")
            self.assertEqual(len(_group(int(mark.read_text()))), 1)
        finally:
            p.kill()
            p.wait()
        self.assertEqual(_left(mark), [])

    def test_views_and_later_commands_in_another_folder(self):
        (self.root / "2024").mkdir()

        def run(words, cwd, env, how):
            if words == ["new-year", "2025"]:
                (cwd / ".." / "2025").mkdir()
            elif words[0] == "run":
                (cwd / "work.txt").write_text(f"run at width {env['TAXJSON_WIDTH']}\n")
            elif words[0] == "handoff":
                (cwd / "handed.txt").write_text("x\n")
            return {"stdout": b"", "stderr": b""}, 0
        views = ["sum", {"argv": "sum", "width": 120}, "new-year 2025",
                 {"cwd": "../2025", "argv": "run --no-input", "width": 120},
                 {"cwd": "../2025", "argv": "handoff"}]
        folder = self.tmp / "views"
        folder.mkdir()
        states = BP.view_states(views, self.root, "2024",
                                ["../2025: handoff", "../2025: run --no-input", "../2025: align --from 2024"],
                                folder, AT, run=run)
        self.assertEqual(sorted(states), ["../2025: align --from 2024", "../2025: handoff", "../2025: run --no-input"])
        self.assertTrue((states["../2025: run --no-input"] / "2025").is_dir())
        self.assertFalse((states["../2025: run --no-input"] / "2025" / "work.txt").exists())
        self.assertEqual((states["../2025: handoff"] / "2025" / "work.txt").read_text(), "run at width 120\n")
        self.assertFalse((states["../2025: handoff"] / "2025" / "handed.txt").exists())
        # not a view: every view before it
        self.assertTrue((states["../2025: align --from 2024"] / "2025" / "handed.txt").exists())
        self.assertEqual({p.stat().st_mtime_ns for p in states["../2025: handoff"].rglob("*")},
                         {(BP.epoch(AT) - BP.FILE_AGE) * 1_000_000_000})

    def test_the_checkout_recorded(self):
        """Its commit and its files' digest; with no git, every file's but
        the build and cache folders'; a change is another tree, two copies
        of one tree one tree."""
        def git(answers):
            def run(argv, **_kw):
                code, out = answers[argv[3]]
                return subprocess.CompletedProcess(argv, code, out.encode(), b"")
            return run
        (self.root / ".git").mkdir()
        files = (0, "old.txt\0reports/a.sum\0gone.txt\0")
        at = BP.checkout(self.root, git({"rev-parse": (0, "abc123\n"), "ls-files": files}))
        commit, word, digest = at.split()
        self.assertEqual((commit, word, len(digest)), ("abc123", "files", 16))
        for head in ((128, "HEAD\n"), (0, "HEAD\n"), (128, "abc123\n")):
            with self.subTest(head=head):
                self.assertEqual(BP.checkout(self.root, git({"rev-parse": head, "ls-files": files})),
                                 f"uncommitted files {digest}")
        copy = self.tmp / "copy"
        shutil.copytree(self.root, copy)
        self.assertEqual(BP.checkout(copy, git({"rev-parse": (0, "abc123\n"), "ls-files": files})), at)
        (copy / "reports" / "a.sum").write_text("sum.\n")
        self.assertNotEqual(BP.checkout(copy, git({"rev-parse": (0, "abc123\n"), "ls-files": files})), at)
        # no git (Fable P3): an archive as it came
        shutil.rmtree(self.root / ".git")
        nogit = BP.checkout(self.root, git({"rev-parse": (128, ""), "ls-files": (128, "")}))
        self.assertRegex(nogit, r"^no git files [0-9a-f]{16}$")
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "x.pyc").write_bytes(b"\0")
        (self.root / "y.pyc").write_bytes(b"\0")
        for cache in (".mypy_cache", "venv", "x.egg-info"):
            (self.root / cache).mkdir()
            (self.root / cache / "a.json").write_text("{}\n")
        self.assertEqual(BP.checkout(self.root, git({"rev-parse": (128, ""), "ls-files": (0, "")})), nogit)
        (self.root / "old.txt").write_text("new\n")
        self.assertNotEqual(BP.checkout(self.root, git({"rev-parse": (128, ""), "ls-files": (128, "")})), nogit)
        self.assertRegex(BP.checkout(), r"^([0-9a-f]{40}|uncommitted|no git) files [0-9a-f]{16}$")

    def test_the_tool_files(self):
        """A parent's clone gets this commit's copies of each (Fable P4: the
        golden and hermetic helpers the recording imports among them)."""
        self.assertEqual(set(BP.TOOL_FILES), {"scripts/style/byte_proof.py", "tests/_clockshift/sitecustomize.py",
                                              "tests/_shimchain.py", "tests/_golden.py", "tests/_hermetic/__init__.py"})
        for rel in BP.TOOL_FILES:
            self.assertTrue((BP.REPO / rel).is_file(), rel)
        self.assertRegex(BP.tool_version(), r"^[0-9a-f]{16}$")

    def test_a_future_instant_is_refused(self):
        with mock.patch.object(BP, "isolation_problem", lambda: ""), \
                self.assertRaisesRegex(SystemExit, "must be past"):
            BP.record(self.tmp / "r", [], {}, instants=("2999-01-01T00:00:00+00:00",))

    def test_the_commands_file(self):
        f = self.tmp / "argv.txt"
        f.write_text("# step 15 S5\n* sum\n* sum   --details\nstyle-usa estimate --details\n"
                     "demo ../2025:  handoff   --json\ndemo sum --nope   # exit 2\n"
                     "demo close-year # stdin: n\ndemo sum # edit: append ../inputs/margin/x.csv a,b\n\n")
        self.assertEqual(BP.read_argv(f, ["demo", "style-usa", "fuzz-straddle"]),
                         {"demo": ["sum", "sum --details", "../2025: handoff --json", "sum --nope # exit 2",
                                   "close-year # stdin: n", "sum # edit: append ../inputs/margin/x.csv a,b"],
                          "style-usa": ["sum", "sum --details", "estimate --details"], "fuzz-straddle": []})
        self.assertEqual(BP.command_name("../2025: handoff --json"), "2025_handoff_json")
        self.assertEqual(BP.split_command("../2025: handoff"), ("../2025", "handoff"))
        self.assertEqual(BP.split_command("sum --json"), (".", "sum --json"))
        for line, why in (("nobook sum\n", "not a book recorded here"), ("fuzz-straddle sum\n", "engine book"),
                          ("demo sum # edit: chmod x\n", "not a note")):
            f.write_text(line)
            with self.assertRaisesRegex(SystemExit, why):
                BP.read_argv(f, ["demo", "fuzz-straddle"])

    def test_argv_adds_to_the_defaults(self):
        """Fable P2: an --argv file's lines are recorded beside the default
        commands, never in their stead, at every instant; a line recording
        under a default's name with other notes is refused."""
        f = self.tmp / "argv.txt"
        f.write_text("demo sum --details --json\ndemo sum\n")
        books = ["demo"]
        defaults, once = BP.all_commands(books)
        added, once2 = BP.all_commands(books, f)
        self.assertEqual(added["demo"], defaults["demo"] + ["sum --details --json"])
        self.assertEqual(once2, once)
        self.assertNotIn("sum --details --json", once2["demo"])
        f.write_text("demo sum # exit 1\n")
        with self.assertRaisesRegex(SystemExit, "records under the name of"):
            BP.all_commands(books, f)
        f.write_text("demo sum --details --json\n")
        with redirect_stdout(io.StringIO()) as plain:
            BP.main(["--list-commands", "--book", "demo"])
        with redirect_stdout(io.StringIO()) as more:
            BP.main(["--list-commands", "--book", "demo", "--argv", str(f)])
        count = re.compile(r"(\d+) command lines")
        self.assertEqual(int(count.search(more.getvalue()).group(1)), int(count.search(plain.getvalue()).group(1)) + 1)

    def test_a_denied_line_is_refused_before_anything_runs(self):
        """TJ-4495, TJ-4579: whatever an --argv file names, nothing that
        pushes, installs, changes a release channel or reaches the network
        runs, and nothing names a path outside the copy: each denied command
        (plain, after a book, after leading flags, in another folder), the
        denied forms, a tool not allowed, an unreviewed command, a glued -C,
        and an argument or an edit out of the copy (absolute, `~`, climbing
        out, after a short or long option, after `@`, in a list) exit 2
        naming the line, with nothing run and the recording folder empty."""
        lines = [f"demo {pre}{word}" for word in BP.DENIED
                 for pre in ("", "-C . ", "--dir=. ", "-C ../2025 ", "../2025: ")]
        lines += [f"* {word}" for word in BP.DENIED]
        lines += [f"demo tips {w}" for w in ("--online", "--onl", "--on=1")]
        lines += [f"demo parser-check all {w}" for w in ("--update-golden", "--update")]
        lines += [f"demo {tool} work/margin_gains.json" for tool in sorted(set(BP.console_scripts()) - BP.TOOLS)]
        lines += ["demo nosuch", "demo -C /tmp sum", "demo sum --x=/etc/y", "demo sum ~/x",
                  "demo form-export --csv ../../../x.csv", "demo ../../..: sum",
                  f"demo -C{self.tmp}/proj/2024 years", f"demo -C{self.tmp}/proj/2024 run --no-input",
                  "demo -C../.. years", "demo years -C/etc", "demo sum -o/etc/x", "demo redact --out=/etc/x",
                  "demo sum @/etc/passwd", "demo sum --csv=a,/etc/x", "demo sum inputs:/etc",
                  "demo years --dir=../../../..", "demo compare --save ../../../x",
                  "demo form-export --out file:///etc/x", "demo elections margin --hint x=/etc/y",
                  "demo sum # edit: append /etc/x y", "demo sum # edit: remove ../../x",
                  "demo sum # edit: touch ~/x"]
        self.assertGreater(len(lines), 80)
        nothing = mock.Mock(side_effect=AssertionError("ran"))
        dest = self.tmp / "rec"
        dest.mkdir()
        argv = self.tmp / "argv.txt"
        with mock.patch.object(BP, "project", nothing), mock.patch.object(BP, "invoke", nothing), \
                mock.patch.object(BP, "invoke_tty", nothing):
            for line in lines:
                with self.subTest(line=line):
                    argv.write_text(f"# a comment\ndemo sum\n{line}\n", encoding="utf-8")
                    with redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as e:
                        BP.main(["--record", str(dest), "--argv", str(argv), "--book", "demo", "--jobs", "3"])
                    self.assertEqual(e.exception.code, 2)
                    self.assertIn(f"{argv}:3: demo: ", said.getvalue())
                    self.assertRegex(said.getvalue(), r"is denied|not a stand-alone tool|not a `tjs` command|"
                                                      r"outside the copy|above the copy|a glued -C")
                    self.assertEqual(list(dest.iterdir()), [])
            for word in BP.DENIED:     # past read_argv: record and record_command refuse too
                with self.subTest(word=word):
                    with redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as e:
                        BP.record(dest, ["demo"], {"demo": ["sum", f"-C . {word}"]})
                    self.assertEqual(e.exception.code, 2)
                    self.assertIn(f"demo: -C . {word}: `{word}` is denied", said.getvalue())
                    with self.assertRaisesRegex(SystemExit, f"refused, nothing run: `{word}` is denied"):
                        BP.record_command(dest / "d", self.root, ".", f"--dir=. {word} # exit 0", AT, {},
                                          run=nothing)
                    self.assertEqual(list(dest.iterdir()), [])
        nothing.assert_not_called()
        for line in ("demo sum", "demo taxjson-sum-gains work/margin_gains.json", "demo tips --json",
                     "demo ../2025: handoff", "demo form-export --csv ../2025/x.csv", "demo parser-check all",
                     "demo sum # edit: append ../inputs/margin/x.csv a", "demo snapshot --out=$HOME/x"):
            self.assertEqual(BP.refusal(line.partition(" ")[2], "2024"), "", line)

    def test_a_path_through_a_link_out_of_the_copy_is_refused(self):
        """Fable's guard probes of 1d (esc/): an argument or an edit that
        resolves outside the copy through a link in it, refused before
        anything runs (against the copy itself in record_command); a link
        that stays inside is fine."""
        (self.root / "out").symlink_to(self.tmp)
        (self.root / "in").symlink_to(self.root / "reports")
        nothing = mock.Mock(side_effect=AssertionError("ran"))
        for cmd in ("sum --csv out/x.csv", "sum --csv=out/x.csv", "sum -oout/x", "sum out", "sum a,out/x",
                    "sum # edit: append out/x y", "sum # edit: touch out/../p/old.txt"):
            with self.subTest(cmd=cmd):
                self.assertRegex(BP.refusal(cmd, ".", self.root), "outside the copy|above the copy")
                with self.assertRaisesRegex(SystemExit, "refused, nothing run"):
                    BP.record_command(self.tmp / "d", self.root, ".", cmd, AT, {}, run=nothing)
        for cmd in ("sum --csv in/x.csv", "sum # edit: append in/x.txt y", "sum # edit: remove in"):
            self.assertEqual(BP.refusal(cmd, ".", self.root), "", cmd)
        nothing.assert_not_called()

    def test_every_tjs_command_is_denied_or_reviewed(self):
        """A command added to the registry (cli/parser `_build_parser`) fails
        this until it is reviewed: DENIED (it pushes, installs, changes a
        release channel, writes outside the pass's folder or reaches the
        network) or REVIEWED."""
        from taxjson.cli.parser import _build_parser
        _p, sub = _build_parser()
        self.assertEqual(set(BP.DENIED) & BP.REVIEWED, set())
        self.assertEqual(set(sub.choices), set(BP.DENIED) | BP.REVIEWED)
        self.assertEqual(set(BP.DENIED), {"fetch", "channels", "deploy", "promote"})
        for (word, opt) in BP.DENIED_FORMS:
            self.assertIn(word, BP.REVIEWED)
            self.assertIn(opt, sub.choices[word]._option_string_actions)
        self.assertTrue(BP.TOOLS <= set(BP.console_scripts()))

    def test_every_option_is_passed_or_reviewed(self):
        """Fable P2 (TJ-4579): each option of the registry is passed by a
        default line or listed in REVIEWED_OPTIONS with a reason; a new
        option fails this until it is one or the other, and so does an
        entry the lines now pass or the registry lost."""
        commands = {b: BP.default_commands(b) for b in BP._golden.books()}
        self.assertEqual(BP.options_unreviewed(commands), ([], []))
        got = BP.options_reached(commands)
        total = sum(len(o) for o in BP.registry().values())
        self.assertEqual(len(got) + len(BP.REVIEWED_OPTIONS), total)
        self.assertTrue(all(reason for reason in BP.REVIEWED_OPTIONS.values()))
        details = [c for c, o in BP.registry().items() if "--details" in o]
        self.assertEqual([c for c in details if (c, "--details") not in got], [])
        # a line that states a refusal passes no option (TJ-4624)
        self.assertEqual(BP.options_reached({"demo": ["sum --json # exit 2"]}), set())
        self.assertEqual(BP.options_reached({"demo": ["sum --json # exit 1"]}), {("sum", "--json")})
        for key in (("compare", "--details"), ("compare", "--json"), ("compare", "--delete"), ("handoff", "--details"),
                    ("handoff", "--prior"), ("elections", "--hint"), ("opening", "--dry-run"), ("ticker-map", "--write")):
            self.assertIn(key, got)
        with mock.patch.dict(BP.registry(), {"sum": {**BP.registry()["sum"], "--made-up": ("--made-up",)}}):
            self.assertEqual(BP.options_unreviewed(commands), ([("sum", "--made-up")], []))
        stale = {**BP.REVIEWED_OPTIONS, ("sum", "--json"): "passed", ("gone", "--x"): "not there"}
        with mock.patch.object(BP, "REVIEWED_OPTIONS", stale):
            self.assertEqual(BP.options_unreviewed(commands), ([], [("gone", "--x"), ("sum", "--json")]))
        for (cmd, _opt), reason in BP.REVIEWED_OPTIONS.items():
            if reason.startswith("a DENIED command"):
                self.assertIn(cmd, BP.DENIED)
        for key in BP.DENIED_FORMS:
            self.assertIn(key, BP.REVIEWED_OPTIONS)

    # Every place in src/taxjson that runs git, pip, uv or an installer, opens
    # a connection or loads code by name (an SDK, a plugin's entry point, a
    # module), and why the proof may still run the commands that reach it.
    # A second line: every child runs in a network namespace with no
    # address and under a shim that refuses each socket connect and lookup,
    # so a place this misses still reaches nothing (and says so). A new
    # place fails the next test until it is added here.
    _REACHES_OUT = re.compile(
        r"""\[\s*["'](?:git|pip|uv)["']|["']git["'],|ensurepip|install\.sh|"""
        r"""\bsocket\b|\bssl\b|urllib\.request|\burllib3\b|\bimport requests\b|\bfrom requests\b|\bhttpx\b|"""
        r"""\baiohttp\b|http\.client|\bxmlrpc\b|\bftplib\b|\bsmtplib\b|\bwebbrowser\b|"""
        r"""yfinance|ib_insync|\bibapi\b|urlopen|\banthropic\b|\bgenai\b|\bopenai\b|"""
        r"""entry_points|import_module\(|__import__\(\s*["'](?!(?:re|os)["'])""")
    _REVIEWED_PLACES = {
        "bin/_entry.py": "a stand-alone tool's module by its name (taxjson.bin.NAME), from its console script",
        "bin/taxjson_generate_parser.py": "the anthropic and google SDKs: taxjson-generate-parser is not in TOOLS",
        "bin/taxjson_harvest.py": "the price chain (IBKR, yfinance): cache only with TAXJSON_OFFLINE",
        "bin/to_base_curr.py": "rate downloads: cached rates only with TAXJSON_OFFLINE",
        "cli/maintainer.py": "channels, deploy, promote: DENIED; parser-check --update-golden: DENIED_FORMS",
        "cli/plan.py": "harvest's text naming the price chain (lib/price_chain): cache only with TAXJSON_OFFLINE",
        "cli/tips.py": "tips --online's Yahoo Finance probe (DENIED_FORMS; skipped with TAXJSON_OFFLINE too)",
        "lib/channels.py": "git rev-parse/status/describe of this checkout (the build); channels' fetch",
        "lib/checklist.py": "`git status` of the project, read only; the installer's command as text",
        "lib/crypto_prices.py": "crypto price downloads: refused with TAXJSON_OFFLINE",
        "lib/fetchers.py": "the fetcher plugins' entry points: `fetch` is DENIED, the others only list them",
        "lib/install_hint.py": "the installer's command as text",
        "lib/price_chain.py": "IBKR and yfinance: skipped with TAXJSON_OFFLINE",
        "parsers/registry.py": "a parser class by its module's name, from taxjson's own table",
        "pipeline/dispatch.py": "a stage's module by its name, from taxjson's own table",
        "pipeline/stages.py": "a comment naming yfinance (the rates stage runs to_base_curr)",
    }

    def test_every_place_that_reaches_out_is_reviewed(self):
        src = Path(BP.REPO) / "src" / "taxjson"
        found = {f.relative_to(src).as_posix() for f in src.rglob("*.py")
                 if self._REACHES_OUT.search(f.read_text(encoding="utf-8"))}
        self.assertEqual(found, set(self._REVIEWED_PLACES))
        for tool in BP.TOOLS:
            self.assertNotIn(f"bin/{BP.console_scripts()[tool]}.py", found, tool)
        self.assertEqual(BP.child_env(AT, self.tmp, self.tmp)["TAXJSON_OFFLINE"], "1")
        for spelling in ("import socket as s", "from socket import create_connection", '__import__("socket")',
                         'importlib.import_module("socket")', "import anthropic", "from google import genai",
                         'metadata.entry_points(group="taxjson.fetchers")', "import httpx"):
            self.assertRegex(spelling, self._REACHES_OUT)

    # Every place in src/taxjson that colours a terminal or reads NO_COLOR,
    # and the commands that run it (COLOURS: the `nocolor` pass runs on them).
    _COLOUR_PLACES = {
        "bin/taxjson_audit.py": ("audit", "taxjson-audit"),
        "bin/taxjson_diff.py": ("taxjson-diff",),
        "bin/taxjson_explain.py": ("taxjson-explain",),
        "bin/taxjson_sum_gains.py": ("taxjson-sum-gains",),
        "cli/checklist_run.py": (),       # sets NO_COLOR for the checks it runs: no colour of its own
        "cli/explain.py": (),             # audit's --no-color help
    }

    def test_every_command_that_colours_gets_the_nocolor_pass(self):
        src = Path(BP.REPO) / "src" / "taxjson"
        colour = re.compile(r"\\033\[|\\x1b\[|\\u001b\[|\bNO_COLOR\b")
        found = {f.relative_to(src).as_posix() for f in src.rglob("*.py") if colour.search(f.read_text(encoding="utf-8"))}
        self.assertEqual(found, set(self._COLOUR_PLACES))
        self.assertEqual(set().union(*self._COLOUR_PLACES.values()), BP.COLOURS)
        commands = {b: BP.default_commands(b) for b in BP._golden.books()}
        recorded = {BP.subcommand(BP.split_command(c)[1]) for cs in commands.values() for c in cs}
        self.assertTrue(BP.COLOURS <= recorded)
        self.assertTrue(BP.colours(["taxjson-sum-gains", "x.json"]) and BP.colours(["-C", "x", "audit"]))
        self.assertFalse(BP.colours(["sum"]))

    def test_the_default_commands(self):
        cmds = BP.book_commands("demo")
        self.assertIn("sum --json", cmds)
        self.assertEqual([c for c in cmds if BP.split_command(c)[0] != "."],
                         ["../2025: run --no-input", "../2025: handoff", "../2025: handoff --json",
                          "../2025: years --json", "../2025: years --diff 2024 2025 --json",
                          "../2025: align --from 2024 --json", "../2025: amt 2024 --json",
                          "../2025: align --from 2024 --write --all --json"])
        self.assertEqual(set(BP._golden.books()) - {b for b in BP._golden.books() if BP.book_commands(b)},
                         {"options", "fuzz-conservation", "fuzz-straddle", "fuzz-transfer"})
        for book in BP._golden.books():
            with self.subTest(book=book):
                cmds = BP.default_commands(book)
                self.assertEqual(cmds[:len(BP.book_commands(book))], BP.book_commands(book))
                self.assertEqual(len({BP.command_name(c) for c in cmds}), len(cmds))
                self.assertNotIn(BP.RUN, {BP.command_name(c) for c in cmds})
                once = BP.first_instant_only(book)
                self.assertTrue(set(once) <= set(cmds))
                hand = set(BP.book_commands(book)) | {BP.command(*BP.parse_command(c))
                                                      for b, c in BP.EXTRA_COMMANDS if b == book}
                self.assertEqual(set(once), set(cmds) - hand)
        self.assertTrue({b for b, _c in BP.EXTRA_COMMANDS + BP.OPTION_LINES} <= set(BP._golden.books()))
        reached, every, tools = BP.reach({b: BP.default_commands(b) for b in BP._golden.books()})
        self.assertEqual(sorted(set(every) - set(reached)), ["channels", "deploy", "fetch", "promote"])
        self.assertEqual(tools, ["taxjson-audit", "taxjson-diff", "taxjson-explain", "taxjson-fees-sum",
                                 "taxjson-sum-gains", "taxjson-sum-income"])
        self.assertEqual(BP.console_scripts()["taxjson-sum-gains"], "taxjson_sum_gains")
        self.assertEqual(BP._program(["taxjson-sum-gains", "x.json"])[-3:],
                         ["taxjson.bin._entry", "taxjson_sum_gains", "x.json"])
        self.assertEqual(BP._program(["sum"])[-2:], ["taxjson.bin.taxjson_run", "sum"])
        with redirect_stdout(io.StringIO()) as said:
            self.assertEqual(BP.main(["--list-commands"]), 0)
        total = sum(len(o) for o in BP.registry().values())
        got = BP.options_reached({b: BP.default_commands(b) for b in BP._golden.books()})
        self.assertIn(f"reach {len(reached)} of {len(every)} tjs commands", said.getvalue())
        self.assertIn("not reached (4): channels, deploy, fetch, promote", said.getvalue())
        self.assertIn("stand-alone tools (6 of ", said.getvalue())
        self.assertIn(f"options passed: {len(got)} of {total}", said.getvalue())
        self.assertIn(f"(REVIEWED_OPTIONS): {len(BP.REVIEWED_OPTIONS)}", said.getvalue())
        self.assertNotIn("Warning", said.getvalue())
        self.assertEqual(BP.subcommand("-C ../2025 --dir=x handoff --json"), "handoff")
        # every prompt, answered both ways, and the stale-books refusals (TJ-4579)
        extra = [c for _b, c in BP.EXTRA_COMMANDS]
        for cmd in ("close-year", "elections", "run", "align", "checklist"):
            self.assertGreaterEqual(sum(BP.split_command(c)[1].split()[0] == cmd and "# stdin:" in c
                                        for c in extra), 2, cmd)
        for cmd in ("t1135", "sum", "close-year", "check-filed", "form-export"):
            self.assertTrue(any(c.startswith(f"{cmd} # edit: append ") for c in extra), cmd)
        # TJ-4624: compare against a snapshot saved first, and after an edit made since; the
        # refusal of books another taxjson built; ticker-map --write answered both ways
        lines = [BP.parse_command(c) for c in extra]
        self.assertTrue(any(ln.argv == "compare before" and ln.before == ("compare --save before",) and ln.after
                            for ln in lines))
        self.assertTrue(any(ln.argv == "compare before" and ln.before and not ln.after for ln in lines))
        self.assertTrue(any(ln.argv == "compare before" and ("remove", "work/crypto_gains.json", "") in ln.after
                            for ln in lines))       # the reports match, a figure moved
        self.assertTrue(any(ln.argv == "-C other cash" and ln.expect == 1 for ln in lines))
        self.assertEqual(sorted(ln.stdin for ln in lines if ln.argv == "ticker-map --suggest --write"), ["n", "y"])


class TestTheNetwork(_Project):
    """TJ-4579 (Fable P2): no child reaches the network. Where no namespace
    can be made, nothing is recorded and no child runs."""

    def test_no_namespace_no_recording(self):
        why = "no `unshare` (util-linux) on PATH"
        nothing = mock.Mock(side_effect=AssertionError("ran"))
        with mock.patch.object(BP, "isolation_problem", lambda: why), mock.patch.object(BP, "project", nothing):
            with self.assertRaisesRegex(SystemExit, "no network namespace can be made here.*util-linux"):
                BP.record(self.tmp / "r", ["demo"], {"demo": ["sum"]})
            self.assertFalse((self.tmp / "r").exists())
            for how in ("apart", "merged", "tty", "outpiped"):
                with self.assertRaisesRegex(SystemExit, "no child runs outside a network namespace"):
                    BP.invoke(["sum"], self.tmp, {}, how)
        with mock.patch.object(BP.shutil, "which", lambda *_a, **_k: None):
            self.assertIn("no `setpriv` and `unshare`", BP.isolation_problem.__wrapped__())
        nothing.assert_not_called()
        proof = json.loads((self.recording("a", fake()) / BP.PROOF).read_text())
        self.assertEqual(proof["isolation"], BP.isolation())
        self.assertEqual(proof["isolation"]["namespace"],
                         "setpriv --pdeathsig KILL -- unshare --user --map-current-user --net --")
        with mock.patch.object(BP.shutil, "which", lambda name, **_k: f"/bin/{name}"):
            self.assertEqual(BP._wrap(["x"]), ["/bin/setpriv", "--pdeathsig", "KILL", "--", "/bin/unshare", "--user",
                                               "--map-current-user", "--net", "--", "x"])
        self.assertEqual(set(proof["isolation"]["refused"]), BP.NETWORK_EVENTS)

    @needs_namespace
    def test_a_child_that_reaches_out_fails_closed_and_says_so(self):
        """Fable's listener mutant: a child that connects to a port on this
        machine, opens a URL and looks a name up reaches nothing, and its
        output and HOME show it; one with the shim skipped (`-I -S`) still
        reaches nothing (the namespace has no address)."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        port = listener.getsockname()[1]
        came = []
        stop = threading.Event()

        def accept():
            while not stop.is_set():
                try:
                    came.append(listener.accept()[0].recv(100))
                except OSError:
                    pass
        t = threading.Thread(target=accept, daemon=True)
        t.start()
        self.addCleanup(listener.close)
        reach = ("import socket, sys, urllib.request\n"
                 f"for what, f in (('connect', lambda: socket.create_connection(('127.0.0.1', {port}), timeout=3)),\n"
                 f"                ('urlopen', lambda: urllib.request.urlopen('http://127.0.0.1:{port}/x', timeout=3)),\n"
                 "                ('lookup', lambda: socket.getaddrinfo('example.invalid', 80))):\n"
                 "    try:\n        f()\n        print(what, 'connected')\n"
                 "    except Exception as e:\n        print(what, 'failed:', type(e).__name__)\n")
        home = self.tmp / "home"
        home.mkdir()
        env = BP.child_env(AT, home, self.tmp)
        try:
            for argv in ([sys.executable, "-P", "-c", reach], [sys.executable, "-I", "-S", "-c", reach]):
                with self.subTest(argv=argv[1:3]), mock.patch.object(BP, "_program", lambda _w, argv=argv: argv):
                    outs, code = BP.invoke(["x"], self.tmp, env, "apart", timeout=60)
                    self.assertEqual(code, 0, outs)
                    self.assertNotIn(b"connected", outs["stdout"])
                    self.assertEqual(outs["stdout"].count(b" failed: "), 3, outs)
                    if argv[1] == "-P":
                        self.assertIn(b"byte proof: socket.getaddrinfo refused", outs["stderr"])
                        self.assertIn("socket.getaddrinfo refused", (home / BP.NETWORK_NOTE).read_text())
        finally:
            stop.set()
            t.join(5)
        self.assertEqual(came, [])
        # so a recording of such a child is not equal to one that does not try
        quiet = child(code="print('x')")
        loud = child(code=f"import socket\ntry:\n    socket.create_connection(('127.0.0.1', {port}))\n"
                          "except OSError:\n    pass\nprint('x')")
        passes = BP.PASSES[:1]
        code, differ = self.differing(self.recording("q", quiet, {"b": ["sum"]}, passes),
                                      self.recording("l", loud, {"b": ["sum"]}, passes))
        self.assertEqual((code, differ), (1, {f"b/sum/{LABEL}/w0.stderr", f"b/sum/{LABEL}/w0.merged",
                                              f"b/sum/{LABEL}/w0.meta",
                                              f"b/sum/{LABEL}/w0.files/home/{BP.NETWORK_NOTE}"}))

    @needs_namespace
    def test_the_packages_recorded(self):
        pkgs = BP.packages()
        self.assertEqual(pkgs["list"], sorted(pkgs["list"]))
        self.assertTrue(pkgs["list"] and all("==" in p for p in pkgs["list"]))
        self.assertEqual(pkgs["digest"], hashlib.sha256("\n".join(pkgs["list"]).encode()).hexdigest()[:16])


# A made-up module and data file of src/taxjson for the reach gate (TJ-4624):
# BASE's, and HEAD's with a line of each function changed (printing the
# same) and the data file changed.
_ZZ_BASE = '''"""A made-up module."""


def shown(x):
    if x > 0:
        return "positive"
    return "other"


def never():
    return "never"
'''
_ZZ_HEAD = _ZZ_BASE.replace('return "other"', 'return "oth" + "er"').replace('return "never"', 'return "nev" + "er"')
# The child of each tree: `sum` calls shown(1); `sum --zero` shown(0) and
# never(), and reads the data file.
_ZZ_CHILD = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("zz_mod", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.shown(1))
if "--zero" in sys.argv:
    print(mod.shown(0), mod.never(), len(open(sys.argv[2]).read()) > 0)
"""


@needs_namespace
class TestTheReachGate(_Project):
    """TJ-4624, TJ-4629: --compare A B --trees BASE HEAD refuses (exit 4)
    unless each line of src/taxjson the diff changes ran: HEAD's added or
    changed lines in HEAD's recording (made with --reach-base BASE), BASE's
    deleted or replaced lines in BASE's (--reach-head HEAD), traced in
    their children (sys.monitoring; a data file by its open). A changed
    line no command reaches is named, the most specific first; the same
    change with a command line that reaches it passes; a line listed in
    UNREACHABLE by its number and text passes while that line holds the
    text, and refuses once it does not; a `# before:` command's lines do
    not count; a recording not traced, traced as the other side, or of
    another tree, is refused."""

    def tree(self, name, module, data):
        root = self.tmp / name
        (root / BP.SRC).mkdir(parents=True)
        (root / BP.SRC / "zz_mod.py").write_text(module)
        (root / BP.SRC / "zz.toml").write_text(data)
        return root

    def gated(self, name, root, other, side, commands):
        """A recording of `root`'s child traced against `other` as the
        comparison's `side` (as `record` with --reach-base OTHER for HEAD,
        --reach-head OTHER for BASE)."""
        dest = self.tmp / name
        dest.mkdir()
        argv = [sys.executable, "-P", "-c", _ZZ_CHILD,
                *(os.path.realpath(root / BP.SRC / f) for f in ("zz_mod.py", "zz.toml"))]
        this, them = BP.src_files(root), BP.src_files(other)
        BP.reach_begin(dest / ".reach", BP.reach_spec(this, them, root, side))
        try:
            with mock.patch.object(BP, "_program", lambda words: argv + words):
                for c in commands["b"]:
                    BP.record_command(dest / "b" / BP.command_name(c) / LABEL, self.root, ".", c, AT, {},
                                      BP.PASSES[:1])
        finally:
            ran = BP.reach_end()
        self.assertFalse((dest / ".reach").exists())
        BP.write_proof(dest, commands, BP.PASSES[:1], instants=(AT,), pkgs=PKGS, src=BP.src_digests(this),
                       reach=BP.reach_record(this, them, root, side, ran))
        return dest

    def pair(self, suffix, base, head, commands):
        return (self.gated("a" + suffix, base, head, "BASE", commands),
                self.gated("b" + suffix, head, base, "HEAD", commands))

    def test_a_changed_line_no_command_ran_is_refused_until_one_does(self):
        base, head = self.tree("base", _ZZ_BASE, "a = 1\n"), self.tree("head", _ZZ_HEAD, "a = 2\n")
        one = {"b": ["sum"]}
        a, b = self.pair("", base, head, one)
        reach = json.loads((b / BP.PROOF).read_text())["reach"]
        self.assertEqual((reach["side"], sorted(reach["traced"])), ("HEAD", ["zz.toml", "zz_mod.py"]))
        self.assertIn([0, 1], reach["lines"]["zz_mod.py"])     # the module's own code ran
        self.assertEqual(reach["opened"], [])
        self.assertEqual(json.loads((a / BP.PROOF).read_text())["reach"]["side"], "BASE")
        code, lines = BP.compare(a, b, trees=(base, head))
        self.assertEqual(code, BP.REACH_REFUSED, lines)
        self.assertTrue(lines[0].startswith("byte proof: refused by the reach gate: 8 changed line(s)"), lines)
        self.assertTrue(lines[-1].startswith("byte proof: equal ("), lines)    # the bytes said too
        self.assertEqual(lines[1:-1], [
            f"{BP.SRC}/zz.toml:1: a changed line of a file no recorded command opened: a = 2",
            f'{BP.SRC}/zz_mod.py:7: a changed line: return "oth" + "er"',
            f'{BP.SRC}/zz_mod.py:11: a changed line: return "nev" + "er"',
            f"{BP.SRC}/zz.toml:1 (BASE): a changed line of a file no recorded command opened: a = 1",
            f'{BP.SRC}/zz_mod.py:7 (BASE): a changed line: return "other"',
            f'{BP.SRC}/zz_mod.py:11 (BASE): a changed line: return "never"',
            f"{BP.SRC}/zz_mod.py:10: a function whose body changed, never called: def never():",
            f"{BP.SRC}/zz_mod.py:10 (BASE): a function whose body changed, never called: def never():"])
        # a command line that runs them in both trees: the gate passes, and the bytes are equal
        two = {"b": ["sum", "sum --zero"]}
        a, b = self.pair("2", base, head, two)
        code, lines = BP.compare(a, b, trees=(base, head))
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], f"byte proof: reach: every changed line of {BP.SRC} ran (10 line(s) in "
                                    f"2 file(s); 0 listed in {BP.UNREACHABLE})")
        # the lines a `# before:` command ran are not the command's
        before = {"b": ["sum # before: sum --zero"]}
        code, lines = BP.compare(*self.pair("3", base, head, before), trees=(base, head))
        self.assertEqual(code, BP.REACH_REFUSED, lines)
        self.assertIn(f'{BP.SRC}/zz_mod.py:7: a changed line: return "oth" + "er"', lines)
        # TJ-4629: HEAD's run alone no longer passes: BASE recorded untraced is refused
        code, lines = BP.compare(self.gated("a4", base, base, "BASE", two), b, trees=(base, head))
        self.assertEqual(code, BP.REACH_REFUSED, lines)
        self.assertIn(f"{BP.SRC}/zz_mod.py in BASE: not traced: record BASE with `--reach-head HEAD`", lines)
        # HEAD recorded with nothing traced, or traced as BASE: refused
        for name, other, side in (("b4", head, "HEAD"), ("b5", base, "BASE")):
            with self.subTest(name=name):
                code, lines = BP.compare(a, self.gated(name, head, other, side, two), trees=(base, head))
                self.assertEqual(code, BP.REACH_REFUSED, lines)
                self.assertIn(f"{BP.SRC}/zz_mod.py: not traced: record HEAD with `--reach-base BASE`", lines)
        # a tree that is not the one recorded
        code, lines = BP.compare(a, b, trees=(head, base))
        self.assertEqual(code, 2)
        self.assertIn("BASE", lines[0])
        self.assertIn("is not the tree its recording was made of", lines[0])

    def test_a_listed_line_passes_while_it_holds_its_text(self):
        """TJ-4636: an entry names one line of HEAD (or, `BASE `, of BASE) by
        its number and its text, and excuses that line only."""
        base, head = self.tree("base", _ZZ_BASE, "a = 1\n"), self.tree("head", _ZZ_HEAD, "a = 2\n")
        listed = head / BP.UNREACHABLE
        listed.parent.mkdir(parents=True)
        listed.write_text("# made up\n"
                          f'{BP.SRC}/zz_mod.py:7 :: return "oth" + "er" :: made up: no line passes 0\n'
                          f"{BP.SRC}/zz_mod.py:10 :: def never(): :: made up: never called\n"
                          f'{BP.SRC}/zz_mod.py:11 :: return "nev" + "er" :: made up: never called\n'
                          f"{BP.SRC}/zz.toml:1 :: a = 2 :: made up: read by no command\n"
                          f'BASE {BP.SRC}/zz_mod.py:7 :: return "other" :: made up: no line passes 0\n'
                          f"BASE {BP.SRC}/zz_mod.py:10 :: def never(): :: made up: never called\n"
                          f'BASE {BP.SRC}/zz_mod.py:11 :: return "never" :: made up: never called\n'
                          f"BASE {BP.SRC}/zz.toml:1 :: a = 1 :: made up: read by no command\n")
        one = {"b": ["sum"]}
        a, b = self.pair("", base, head, one)
        code, lines = BP.compare(a, b, trees=(base, head))
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], f"byte proof: reach: every changed line of {BP.SRC} ran (10 line(s) in "
                                    f"2 file(s); 8 listed in {BP.UNREACHABLE})")
        # the line changed again: its entry no longer matches, and refuses
        (head / BP.SRC / "zz_mod.py").write_text(_ZZ_HEAD.replace('"nev" + "er"', '"ne" + "ver"'))
        code, lines = BP.compare(*self.pair("2", base, head, one), trees=(base, head))
        self.assertEqual(code, BP.REACH_REFUSED, lines)
        self.assertIn(f'{BP.SRC}/zz_mod.py:11: a changed line: return "ne" + "ver"', lines)
        self.assertIn(f'{BP.UNREACHABLE}:4: `return "nev" + "er"` is not line 11 of {BP.SRC}/zz_mod.py', lines)
        self.assertNotIn(f"{BP.SRC}/zz_mod.py:10: a function whose body changed, never called: def never():", lines)
        # an entry by its text alone, with no reason, or not of src/taxjson
        for bad in (f"{BP.SRC}/zz.toml:1 :: a = 2 ::  ", "zz.toml:1 :: a = 2 :: made up",
                    f"{BP.SRC}/zz.toml :: a = 2 :: made up", f"{BP.SRC}/zz.toml:1 :: a = 2",
                    f"HEAD {BP.SRC}/zz.toml:1 :: a = 2 :: made up"):
            with self.subTest(bad=bad):
                listed.write_text(bad + "\n")
                self.assertEqual(BP.read_unreachable(head, {"HEAD": BP.src_files(head)}),
                                 (set(), [f"{BP.UNREACHABLE}:1: not `[BASE ]{BP.SRC}/PATH:LINE :: TEXT :: REASON`"]))
        listed.write_text(f"{BP.SRC}/zz_mod.py:2 :: x = a :: b :: the text holds the separator\n")
        self.assertEqual(BP.read_unreachable(head, {"HEAD": {"zz_mod.py": b"x = 1\n    x = a :: b\n"}}),
                         ({("HEAD", "zz_mod.py", 2)}, []))


# The reach gate's soundness cases (TJ-4629 to TJ-4636, Fable's fourth
# review: its gate_probe cases, and the handler mutants of its mkmut.py in
# small): src/taxjson/m.py of BASE and of HEAD, the calls recorded (each a
# command), the call on which the change shows, and what --compare says:
# its exit, and lines among those it names. A case refused shows its change
# only on a call the recording never makes; the gate names what the rule
# needs. Each child prints what m's call prints.
_CASE_CHILD = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
eval(json.loads(sys.argv[2])[int(sys.argv[-1][3:])], {"m": m})
"""
_D = textwrap.dedent
_LOOP = "a changed line in a loop, never run in two iterations of one entry of it"
_BRANCH = "a changed branch not taken both ways"
_M = f"{BP.SRC}/m.py"
_YEARS = '''
    def main(rows):
        for r in rows:
            line = f"{r['year']}  open"
            if r["problem"]:
                line += f"; {r['problem']}"
            print(line)
    '''
_READ = '''
    import os, tomllib
    def main(v):
        with open(os.path.join(os.path.dirname(__file__), "d.toml"), "rb") as f:
            print(tomllib.load(f)[v])
    '''
_ODD = '''
    def a(v):
        if v == 7:
            print("seven")
            return
        print("a")
    def main(v):
        if v:
            print("Error: refused")
            return
        a(v)
    '''
_CASES = {
    "c01 an arm of a conditional on a line that ran (TJ-4630)": dict(
        base='def main(v):\n    print("ok" if v else "bad")\n', head='def main(v):\n    print("ok" if v else "BAD")\n',
        calls=["m.main(1)"], shows="m.main(0)", code=4,
        said=[f'{_M}:2: {_BRANCH}: print("ok" if v else "BAD")']),
    "c02 an else deleted (TJ-4629)": dict(
        base='''
        def main(v):
            if v:
                print("ok")
            else:
                print("Warning: nothing")
            print("end")
        ''', head='''
        def main(v):
            if v:
                print("ok")
            print("end")
        ''', calls=["m.main(1)"], shows="m.main(0)", code=4,
        said=[f"{_M}:5 (BASE): a changed clause line, the first statement of its body never run: else:",
              f'{_M}:6 (BASE): a changed line: print("Warning: nothing")']),
    "c03 an except deleted (TJ-4629, TJ-4632)": dict(
        base='''
        def main(v):
            try:
                x = int(v)
            except ValueError:
                print("Error: not a number")
                return
            print(x)
        ''', head='''
        def main(v):
            x = int(v)
            print(x)
        ''', calls=["m.main('1')"], shows="m.main('x')", code=4,
        said=[f"{_M}:5 (BASE): {_BRANCH}: except ValueError:",
              f"{_M}:3: `x = int(v)` moved into or out of a try, a finally or a with (BASE: try ValueError; "
              "HEAD: none): x = int(v)"]),
    "c04 an if that refused, deleted (TJ-4629)": dict(
        base='''
        def main(v):
            if v > 5:
                print("Error: too big")
                return
            print(v)
        ''', head='''
        def main(v):
            print(v)
        ''', calls=["m.main(1)"], shows="m.main(9)", code=4,
        said=[f"{_M}:3 (BASE): {_BRANCH}: if v > 5:", f'{_M}:4 (BASE): a changed line: print("Error: too big")']),
    "c05 a message table's entry no call printed (TJ-4633)": dict(
        base='MSG = {\n    "a": "alpha",\n    "b": "beta",\n}\ndef main(v):\n    print(MSG[v])\n',
        head='MSG = {\n    "a": "alpha",\n    "b": "BETA",\n}\ndef main(v):\n    print(MSG[v])\n',
        calls=["m.main('a')"], shows="m.main('b')", code=4,
        said=[f'{_M}:3: a changed text no recorded output shows (`BETA`): "b": "BETA",']),
    "c06 a default changed (TJ-4630)": dict(
        base='def fmt(x, places=2):\n    return f"{x:.{places}f}"\ndef main(v):\n    print(fmt(1.25, 1) if v else fmt(1.25))\n',
        head='def fmt(x, places=3):\n    return f"{x:.{places}f}"\ndef main(v):\n    print(fmt(1.25, 1) if v else fmt(1.25))\n',
        calls=["m.main(1)"], shows="m.main(0)", code=4,
        said=[f"{_M}:1: a changed default (`places`) no call used: def fmt(x, places=3):"]),
    "c09 a generator not resumed": dict(
        base='def gen():\n    yield "a"\n    yield "b"\ndef main(v):\n    g = gen()\n    print(next(g))\n    if v:\n        print(next(g))\n',
        head='def gen():\n    yield "a"\n    yield "B"\ndef main(v):\n    g = gen()\n    print(next(g))\n    if v:\n        print(next(g))\n',
        calls=["m.main(0)"], shows="m.main(1)", code=4, said=[f'{_M}:3: a changed line: yield "B"']),
    "c10 a lambda on a line other code ran (TJ-4634)": dict(
        base='H = {"a": lambda: "alpha", "b": lambda: "beta"}\ndef main(v):\n    print(H[v]())\n',
        head='H = {"a": lambda: "alpha", "b": lambda: "BETA"}\ndef main(v):\n    print(H[v]())\n',
        calls=["m.main('a')"], shows="m.main('b')", code=4,
        said=[f'{_M}:1: a changed line: a lambda or generator on it never run: H = {{"a": lambda: "alpha", '
              '"b": lambda: "BETA"}']),
    "c11 a format no recorded value shows: escapes, as CONTRIBUTING says": dict(
        base='def main(v):\n    print(", ".join(f"{x:,.2f}" for x in v))\n',
        head='def main(v):\n    print(", ".join(f"{x:.2f}" for x in v))\n',
        calls=["m.main([1.5, 999])"], shows="m.main([1234.5])", code=0),
    "c12 a continuation line in an arm no call took (TJ-4634)": dict(
        base='def main(v):\n    msg = ("fine" if v else\n           "problem: see "\n           "the details")\n    print(msg)\n',
        head='def main(v):\n    msg = ("fine" if v else\n           "problem: see "\n           "THE DETAILS")\n    print(msg)\n',
        calls=["m.main(1)"], shows="m.main(0)", code=4,
        said=[f'{_M}:4: a changed line in an arm or operand: the test before it not taken both ways: "THE DETAILS")',
              f'{_M}:4: a changed line of a statement, the part of it there never run: "THE DETAILS")']),
    "c13 a statement moved out of its try (TJ-4632)": dict(
        base='''
        def main(v):
            try:
                a = int(v)
                b = 10 // a
            except ZeroDivisionError:
                print("Error: zero")
                return
            print(b)
        ''', head='''
        def main(v):
            try:
                a = int(v)
            except ZeroDivisionError:
                print("Error: zero")
                return
            b = 10 // a
            print(b)
        ''', calls=["m.main('2')"], shows="m.main('0')", code=4,
        said=[f"{_M}:8: `b = 10 // a` moved into or out of a try, a finally or a with (BASE: try "
              "ZeroDivisionError; HEAD: none): b = 10 // a"]),
    "c14 blanks ending a line of a string (TJ-4635)": dict(
        base='HELP = """Usage:   \nmore"""\ndef main(v):\n    print(repr(HELP) if v else "x")\n',
        head='HELP = """Usage:\nmore"""\ndef main(v):\n    print(repr(HELP) if v else "x")\n',
        calls=["m.main(0)"], shows="m.main(1)", code=4,
        said=[f'{_M}:1: a changed text no recorded output shows (`Usage: more`): HELP = """Usage:']),
    "c16 a function called from C: a difference": dict(
        base='def key(x):\n    return -x\ndef main(v):\n    print(sorted(v, key=key))\n',
        head='def key(x):\n    return x\ndef main(v):\n    print(sorted(v, key=key))\n',
        calls=["m.main([1, 2])"], shows="m.main([2, 1])", code=1),
    "c18 a closure that reads the loop's variable later, one row (TJ-4631)": dict(
        base="def main(v):\n    for r in v:\n        print(r)\n",
        head="def main(v):\n    shows = []\n    for r in v:\n        shows.append(lambda: print(r))\n"
             "    for s in shows:\n        s()\n",
        calls=["m.main([1])"], shows="m.main([1, 2])", code=4,
        said=[f"{_M}:4: {_LOOP}: shows.append(lambda: print(r))"]),
    "c18b the same, two rows: a difference": dict(
        base="def main(v):\n    for r in v:\n        print(r)\n",
        head="def main(v):\n    shows = []\n    for r in v:\n        shows.append(lambda: print(r))\n"
             "    for s in shows:\n        s()\n",
        calls=["m.main([1, 2])"], shows="m.main([1, 2])", code=1),
    "c18L the same in a loop longer than one byte's jump (TJ-4631)": dict(
        base="def main(v):\n    for r in v:\n" + "".join(f"        x{i} = r + {i}\n" for i in range(150)) + "        print(r)\n",
        head="def main(v):\n    shows = []\n    for r in v:\n" + "".join(f"        x{i} = r + {i}\n" for i in range(150))
             + "        shows.append(lambda: print(r))\n    for s in shows:\n        s()\n",
        calls=["m.main([1])"], shows="m.main([1, 2])", code=4,
        said=[f"{_M}:154: {_LOOP}: shows.append(lambda: print(r))"]),
    "c18M the same, two rows: a difference": dict(
        base="def main(v):\n    for r in v:\n" + "".join(f"        x{i} = r + {i}\n" for i in range(150)) + "        print(r)\n",
        head="def main(v):\n    shows = []\n    for r in v:\n" + "".join(f"        x{i} = r + {i}\n" for i in range(150))
             + "        shows.append(lambda: print(r))\n    for s in shows:\n        s()\n",
        calls=["m.main([1, 2])"], shows="m.main([1, 2])", code=1,
        unsaid=[f"{_M}:154: {_LOOP}: shows.append(lambda: print(r))"]),
    "c22 a try's else made its finally (TJ-4632)": dict(
        base='''
        def main(v):
            try:
                x = int(v)
            except ValueError:
                x = 0
            else:
                print("a number")
            print(x)
        ''', head='''
        def main(v):
            try:
                x = int(v)
            except ValueError:
                x = 0
            finally:
                print("a number")
            print(x)
        ''', calls=["m.main('1')"], shows="m.main('x')", code=4,
        said=[f"{_M}:8: `print('a number')` moved into or out of a try, a finally or a with (BASE: try else; "
              "HEAD: finally): print(\"a number\")",
              f"{_M}:4: `x = int(v)` moved into or out of a try, a finally or a with (BASE: try ValueError; "
              "HEAD: try ValueError (and a finally)): x = int(v)"]),
    "c26 a module docstring's later line, printed by no call (TJ-4633)": dict(
        base='"""A tool.\n\nIts help text line.\n"""\ndef main(v):\n    print(__doc__ if v else "x")\n',
        head='"""A tool.\n\nIts HELP text line.\n"""\ndef main(v):\n    print(__doc__ if v else "x")\n',
        calls=["m.main(0)"], shows="m.main(1)", code=4,
        said=[f"{_M}:3: a changed text no recorded output shows (`A tool. Its HELP text line.`): Its HELP text line."]),
    "c27 a method never called": dict(
        base='class K:\n    def a(self):\n        return "a"\n    def b(self):\n        return "b"\n'
             "def main(v):\n    print(K().b() if v else K().a())\n",
        head='class K:\n    def a(self):\n        return "a"\n    def b(self):\n        return "B"\n'
             "def main(v):\n    print(K().b() if v else K().a())\n",
        calls=["m.main(0)"], shows="m.main(1)", code=4, said=[f'{_M}:5: a changed line: return "B"']),
    "c28 a data file's changed line opened, never shown (TJ-4633)": dict(
        base=_READ, head=_READ, xb={"d.toml": 'a = "alpha"\nb = "beta"\n'}, xh={"d.toml": 'a = "alpha"\nb = "BETA"\n'},
        calls=["m.main('a')"], shows="m.main('b')", code=4,
        said=[f'{BP.SRC}/d.toml:2: a changed line of a file no recorded output shows (`BETA`): b = "BETA"']),
    "c28b the same, the line shown: a difference, the gate passes": dict(
        base=_READ, head=_READ, xb={"d.toml": 'a = "alpha"\nb = "beta"\n'}, xh={"d.toml": 'a = "alpha"\nb = "BETA"\n'},
        calls=["m.main('a')", "m.main('b')"], shows="m.main('b')", code=1,
        said=[f"byte proof: reach: every changed line of {BP.SRC} ran (2 line(s) in 1 file(s); 0 listed in "
              f"{BP.UNREACHABLE})"]),
    "c29 a data file deleted (TJ-4629)": dict(
        base=_READ.replace('"d.toml"', 'v + ".toml"').replace("[v]", ""),
        head=_READ.replace('"d.toml"', 'v + ".toml"').replace("[v]", ""),
        xb={"a.toml": "x = 1\n", "b.toml": "y = 2\n"}, xh={"a.toml": "x = 1\n", "b.toml": None},
        calls=["m.main('a')"], shows="m.main('b')", code=4,
        said=[f"{BP.SRC}/b.toml:1 (BASE): a changed line of a file no recorded command opened: y = 2"]),
    "c30 an entry by its text alone excuses nothing (TJ-4636)": dict(
        base=_ODD.replace('"Error: refused")\n            return', '"Error: refused")\n            raise SystemExit(3)'),
        head=_ODD.replace("refused", "REFUSED"), calls=["m.main(0)"], shows="m.main(1)", code=4,
        unreachable=f"{BP.SRC}/m.py :: return :: made up: a(7) is never passed\n",
        said=[f"{_M}:10: a changed line: return",
              f"{BP.UNREACHABLE}:1: not `[BASE ]{BP.SRC}/PATH:LINE :: TEXT :: REASON`"]),
    "c30b each entry excuses its own line (TJ-4636)": dict(
        base=_ODD.replace('"Error: refused")\n            return', '"Error: refused")\n            raise SystemExit(3)'),
        head=_ODD.replace("refused", "REFUSED"), calls=["m.main(0)"], shows="m.main(1)", code=4,
        unreachable=f"{BP.SRC}/m.py:5 :: return :: made up: a(7) is never passed\n"
                    f'{BP.SRC}/m.py:9 :: print("Error: REFUSED") :: made up: no call passes 1\n',
        said=[f"{_M}:10: a changed line: return", f'{_M}:9 (BASE): a changed line: print("Error: refused")'],
        unsaid=[f'{_M}:9: a changed line: print("Error: REFUSED")']),
    "c31 the coding cookie (TJ-4635)": dict(
        base=b'# -*- coding: latin-1 -*-\ndef main(v):\n    print(len("\xc3\xa9") if v else "x")\n',
        head=b'# -*- coding: utf-8 -*-\ndef main(v):\n    print(len("\xc3\xa9") if v else "x")\n',
        calls=["m.main(0)"], shows="m.main(1)", code=4,
        said=[f'{_M}:3: {_BRANCH}: print(len("\xe9") if v else "x")']),
    "M0 a pure move into a helper, both arms run: passes": dict(
        base='''
        def main(rows):
            for r in rows:
                run = (f"last run {r['last']}"
                       if r["last"] else "not run yet")
                print(r["year"], run)
        ''', head='''
        def _last_run(r):
            return (f"last run {r['last']}"
                    if r["last"] else "not run yet")


        def main(rows):
            for r in rows:
                run = _last_run(r)
                print(r["year"], run)
        ''', calls=["m.main([{'year': 2024, 'last': '09:30'}, {'year': 2025, 'last': ''}])"],
        shows="m.main([])", code=0),
    "M2 a closure in the years loop, one year recorded (TJ-4631)": dict(
        base=_YEARS, head='''
        def _print(line):
            print(line)


        def main(rows):
            shows = []
            for r in rows:
                line = f"{r['year']}  open"
                shows.append(lambda: _print(line))
            for show in shows:
                show()
        ''', calls=["m.main([{'year': 2024, 'problem': ''}])"],
        shows="m.main([{'year': 2024, 'problem': ''}, {'year': 2025, 'problem': ''}])", code=4,
        said=[f"{_M}:10: {_LOOP}: shows.append(lambda: _print(line))"]),
    "M4 a call moved out of its try (TJ-4632)": dict(
        base='''
        class LayoutError(Exception):
            pass
        def compare(a, b):
            if a == b:
                raise LayoutError("same")
            return a + b
        def main(a, b):
            try:
                c = compare(a, b)
            except LayoutError as e:
                print("Error:", e)
                return
            print(c)
        ''', head='''
        class LayoutError(Exception):
            pass
        def compare(a, b):
            if a == b:
                raise LayoutError("same")
            return a + b
        def _compared(a, b):
            return compare(a, b)
        def main(a, b):
            c = _compared(a, b)
            try:
                print(c)
            except LayoutError as e:
                print("Error:", e)
                return
        ''', calls=["m.main(1, 2)"], shows="m.main(1, 1)", code=4,
        said=[f"{_M}:9: `compare(a, b)` moved into or out of a try, a finally or a with (BASE: try "
              "LayoutError; HEAD: none): return compare(a, b)"]),
    "M6 a helper's arm on a line that ran (TJ-4630)": dict(
        base='''
        def main(rows):
            for r in rows:
                run = (f"last run {r}"
                       if r else "not run yet")
                print(run)
        ''', head='''
        def _last_run(r):
            return (f"last run {r}"
                    if r else "never run")


        def main(rows):
            for r in rows:
                print(_last_run(r))
        ''', calls=["m.main([1, 2])"], shows="m.main([0])", code=4,
        said=[f'{_M}:4: {_BRANCH}: if r else "never run")',
              f'{_M}:4: a changed text no recorded output shows (`never run`): if r else "never run")']),
    "M7 a move that drops a branch no row took (TJ-4629, TJ-4630)": dict(
        base=_YEARS, head='''
        def _print(line):
            print(line)


        def main(rows):
            for r in rows:
                _print(f"{r['year']}  open")
        ''', calls=["m.main([{'year': 2024, 'problem': ''}, {'year': 2025, 'problem': ''}])"],
        shows="m.main([{'year': 2024, 'problem': 'stale'}])", code=4,
        said=[f"{_M}:5 (BASE): {_BRANCH}: if r[\"problem\"]:",
              f"{_M}:6 (BASE): a changed line: line += f\"; {{r['problem']}}\""]),
    "M8 a deletion alone (TJ-4629)": dict(
        base=_YEARS, head=_YEARS.replace('''            if r["problem"]:\n                line += f"; {r['problem']}"\n''', ""),
        calls=["m.main([{'year': 2024, 'problem': ''}, {'year': 2025, 'problem': ''}])"],
        shows="m.main([{'year': 2024, 'problem': 'stale'}])", code=4,
        said=[f"{_M}:5 (BASE): {_BRANCH}: if r[\"problem\"]:",
              f"{_M}:6 (BASE): a changed line: line += f\"; {{r['problem']}}\""]),
    "S1 an added line every call runs both ways: passes": dict(
        base='def main(v):\n    print("value", v)\n',
        head='def main(v):\n    word = "value" if v else "value"\n    print(word, v)\n',
        calls=["m.main(1)", "m.main(0)"], shows="m.main(2)", code=0),
    "S2 a helper called inside the try: passes": dict(
        base='''
        def main(v):
            try:
                x = int(v)
            except ValueError:
                x = -1
            print("parsed", x)
        ''', head='''
        def _parsed(v):
            return int(v)
        def main(v):
            try:
                x = _parsed(v)
            except ValueError:
                x = -1
            print("parsed", x)
        ''', calls=["m.main('1')", "m.main('x')"], shows="m.main('2')", code=0),
    "S3 a comment changed, its module imported (TJ-4635): passes": dict(
        base='def main(v):\n    print("value", v)  # one\n', head='def main(v):\n    print("value", v)  # two\n',
        calls=["m.main(1)"], shows="m.main(2)", code=0),
    "S4 a default added, used and passed: passes": dict(
        base='def fmt(x, places=2):\n    return f"{x:.{places}f}"\ndef main(v):\n    print(fmt(1.5, 1) if v else fmt(1.5))\n',
        head='def fmt(x, places=2, unit=""):\n    return f"{x:.{places}f}{unit}"\n'
             "def main(v):\n    print(fmt(1.5, 1) if v else fmt(1.5))\n",
        calls=["m.main(1)", "m.main(0)"], shows="m.main(1)", code=0),
}


@needs_namespace
class TestTheGateIsSound(_Project):
    """Fable's fourth review (TJ-4629 to TJ-4636): each case of `_CASES`
    recorded from BASE and HEAD, both traced, through real children; what
    --compare says is the case's. Each case refused shows its change on a
    call the recording does not make (checked: the two trees print other
    bytes there), so a gate that passed it would pass a visible change;
    each control is a byte difference; the pure move and the sound changes
    pass, and c11 (a format no recorded value reaches) passes as the
    documented limit."""

    def case_tree(self, name, text, extra, unreachable=None):
        root = self.tmp / name
        (root / BP.SRC).mkdir(parents=True)
        data = text if isinstance(text, bytes) else _D(text).encode()
        (root / BP.SRC / "m.py").write_bytes(data)
        for rel, body in (extra or {}).items():
            if body is not None:
                (root / BP.SRC / rel).write_text(body)
        if unreachable:
            (root / BP.UNREACHABLE).parent.mkdir(parents=True)
            (root / BP.UNREACHABLE).write_text(unreachable)
        return root

    def case_recording(self, name, root, other, side, calls):
        dest = self.tmp / name
        dest.mkdir()
        argv = [sys.executable, "-P", "-c", _CASE_CHILD, os.path.realpath(root / BP.SRC / "m.py"), json.dumps(calls)]
        commands = {"b": [f"sum --c{i}" for i in range(len(calls))]}
        this, them = BP.src_files(root), BP.src_files(other)
        BP.reach_begin(dest / ".reach", BP.reach_spec(this, them, root, side))
        try:
            with mock.patch.object(BP, "_program", lambda words: argv + words):
                for c in commands["b"]:
                    BP.record_command(dest / "b" / BP.command_name(c) / LABEL, self.root, ".", c, AT, {},
                                      BP.PASSES[:1])
        finally:
            ran = BP.reach_end()
        BP.write_proof(dest, commands, BP.PASSES[:1], instants=(AT,), pkgs=PKGS, src=BP.src_digests(this),
                       reach=BP.reach_record(this, them, root, side, ran))
        return dest

    def shown_on(self, root, call):
        r = subprocess.run([sys.executable, "-P", "-c", _CASE_CHILD, os.path.realpath(root / BP.SRC / "m.py"),
                            json.dumps([call]), "--c0"], capture_output=True, timeout=60, check=False)
        return r.returncode, r.stdout

    def test_each_case(self):
        for n, (label, c) in enumerate(_CASES.items()):
            with self.subTest(case=label):
                base = self.case_tree(f"t{n}a", c["base"], c.get("xb"))
                head = self.case_tree(f"t{n}b", c["head"], c.get("xh"), c.get("unreachable"))
                if c["code"] in (1, 4) or label.startswith("c11"):
                    self.assertNotEqual(self.shown_on(base, c["shows"]), self.shown_on(head, c["shows"]))
                a = self.case_recording(f"r{n}a", base, head, "BASE", c["calls"])
                b = self.case_recording(f"r{n}b", head, base, "HEAD", c["calls"])
                code, lines = BP.compare(a, b, trees=(base, head))
                self.assertEqual(code, c["code"], lines)
                for line in c.get("said", ()):
                    self.assertIn(line, lines)
                for line in c.get("unsaid", ()):
                    self.assertNotIn(line, lines)
                if c["code"] == 0:
                    self.assertTrue(lines[-1].startswith(f"byte proof: reach: every changed line of {BP.SRC} ran"),
                                    lines)


class TestTheReachRules(unittest.TestCase):
    """What a changed line of src/taxjson needs (TJ-4624, TJ-4629 to
    TJ-4635, `file_requirements`): on each side, its own line where an
    instruction is, in each code object on it; else the innermost part of
    its statement that has one and each conditional around that part (a
    string spanning lines; else its function called: a docstring); a
    clause keyword line the first statement of its body; a function's
    header, its function called, and a default on it used; each branch on
    it taken both ways; in a loop's body, two iterations of one entry; a
    deletion in HEAD, the statement beside it run; each function whose body
    changed, called; a text, shown; a byte with no code (a comment, a
    blank), its module imported; a statement, the same tries, finallys and
    withs around it."""

    MOD = '''"""module doc"""
import functools


def deco(f):
    return f


@deco
def g(x,
      y=2):
    """g's doc
    second line"""
    s = ("a"
         "b")
    print(s,
          x)
    if x:
        z = 1
        w = 3
    else:
        z = 2
    return z


class K:
    """k doc"""
    a = 1
'''

    @staticmethod
    def needs(base, head, path="m.py"):
        data = [x if x is None or isinstance(x, bytes) else x.encode() for x in (base, head)]
        return sorted({(n.order, n.side, n.row, n.kind, tuple(sorted(n.keys, key=repr)))
                       for n in BP.file_requirements(path, *data, (False, set()))})

    def test_what_each_change_needs(self):
        g_called = [(2, "BASE", 10, "entered", (2,)), (2, "HEAD", 10, "entered", (2,))]
        cases = {
            "a line": (self.MOD.replace("          x)", "          x, 1)"),
                       [(0, "BASE", 17, "line", ((2, 17),)), (0, "HEAD", 17, "line", ((2, 17),))] + g_called),
            "a docstring": (self.MOD.replace("second line", "second line!"),
                            [(0, "BASE", 13, "entered", (2,)), (0, "HEAD", 13, "entered", (2,))] + g_called),
            "a comment": (self.MOD.replace("    s = (", "    # a note\n    s = ("),
                          [(3, "HEAD", 14, "module", ())]),
            "a blank line": (self.MOD.replace("    if x:", "\n    if x:"), [(3, "HEAD", 18, "module", ())]),
            "a string's later line": (self.MOD.replace('"b")', '"c")'),
                                      [(0, "BASE", 15, "rows", ((2, 14),)), (0, "HEAD", 15, "rows", ((2, 14),))]
                                      + g_called),
            "a header": (self.MOD.replace("      y=2):", "      y=3):"),
                         [(0, "BASE", 11, "default", ((2, "y"),)), (0, "BASE", 11, "entered", (2,)),
                          (0, "HEAD", 11, "default", ((2, "y"),)), (0, "HEAD", 11, "entered", (2,))]),
            "a class body": (self.MOD.replace("    a = 1", "    a = 2"),
                             [(0, "BASE", 28, "line", ((3, 28),)), (0, "HEAD", 28, "line", ((3, 28),))]),
            "a line deleted": (self.MOD.replace("        w = 3\n", ""),
                               [(0, "BASE", 20, "line", ((2, 20),)), (1, "HEAD", 19, "rows", ((2, 19),))] + g_called),
            "a block deleted": (self.MOD.replace("    else:\n        z = 2\n", ""),
                                [(0, "BASE", 21, "rows", ((2, 22),)), (0, "BASE", 22, "line", ((2, 22),)),
                                 (1, "HEAD", 18, "rows", ((2, 18),))] + g_called),
        }
        for label, (head, want) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(self.needs(self.MOD, head), sorted(want))

    def test_every_byte_is_a_change(self):
        """TJ-4635: blanks ending a line of a string spanning lines, the
        coding cookie (and so how a line reads), a line ending, a blank line
        of a string: each a changed line."""
        self.assertEqual(BP.diff_rows('X = """a   \nb"""\n', 'X = """a\nb"""\n').head, [1])
        self.assertEqual(BP.diff_rows('X = """a\n\nb"""\n', 'X = """a\nb"""\n'), BP.Diff([], [2], [(1, "")], [], []))
        self.assertEqual(BP.diff_rows("x = 1\r\n", "x = 1\n"), BP.Diff([], [], [], [1], [1]))
        self.assertEqual(BP.diff_rows("x = 1  # one\n", "x = 1  # 1\n"), BP.Diff([], [], [], [1], [1]))
        latin, utf8 = (f'# -*- coding: {c} -*-\nX = "\xc3\xa9"\n'.encode("latin-1") for c in ("latin-1", "utf-8"))
        self.assertEqual(self.needs(latin, utf8), [(0, "BASE", 2, "line", ((0, 2),)), (0, "HEAD", 2, "line", ((0, 2),)),
                                                   (3, "BASE", 1, "module", ()), (3, "HEAD", 1, "module", ())])
        # a raw change's module imported: any line of it ran
        need = BP.Need(3, "m.py", 1, "", "module")
        self.assertTrue(BP.satisfied(need, BP._ran({"lines": {"m.py": [[0, 1]]}})))
        self.assertFalse(BP.satisfied(need, BP._ran({"lines": {"n.py": [[0, 1]]}})))

    BRANCHY = '''def f(xs, v):
    if v:
        pass
    elif v is None:
        pass
    while v:
        v -= 1
    for x in xs:
        pass
    y = 1 if v else 2
    z = v and xs
    w = [x for x in xs if x]
    match v:
        case 1:
            pass
        case _:
            pass
    try:
        pass
    except ValueError:
        pass
    assert v
    return y, z, w
'''

    def test_every_branch_on_a_changed_line(self):
        """TJ-4630: if, elif, while, a for's exhaustion, a conditional
        expression, and/or, a comprehension's condition, a match case, an
        except's match: each changed line's branches, both ways; one
        outcome seen is not enough. An assert's test aside: only an error
        goes its other way (its message is a text, TJ-4633)."""
        rows = [2, 4, 6, 8, 10, 11, 12, 14, 20]
        src = BP._source(self.BRANCHY, "m.py")
        on = {pos[0] for _idx, pos, _ins in BP.watched(src, rows + [22]).groups}
        self.assertEqual(sorted(on), rows)
        head = self.BRANCHY.replace("pass", "pass  # x").replace("1 if v", "3 if v")
        branch = [n for n in BP.file_requirements("m.py", self.BRANCHY.encode(), head.encode(), (False, set()))
                  if n.kind == "branch"]
        self.assertEqual({(n.side, n.row) for n in branch}, {("BASE", 10), ("HEAD", 10)})
        need = next(n for n in branch if n.side == "HEAD")
        (idx, at, nxt, fell), = need.keys
        one = {"branches": {"m.py": [[idx, at, nxt]]}}
        both = {"branches": {"m.py": [[idx, at, nxt], [idx, at, nxt + 100]]}}
        self.assertFalse(BP.satisfied(need, BP._ran(one)))
        self.assertTrue(BP.satisfied(need, BP._ran(both)))
        # an and/or whose value is used: each operand's jump both ways, not any two of them
        value = "def f(a, b, c):\n    return (a and b\n            and c)\n"
        src = BP._source(value, "m.py")
        self.assertEqual(len(BP.watched(src, [2]).groups), 2)

    def test_a_line_in_a_loop_needs_two_iterations(self):
        """TJ-4631: a changed line in a loop's or a comprehension's body
        needs two iterations of one entry of the loop (a closure that reads
        the loop's variable later differs only then); one outside, none."""
        base = "def f(v):\n    out = []\n    for r in v:\n        out.append(r)\n    n = [r + 1\n         for r in v]\n    return out, n\n"
        head = base.replace("out.append(r)", "out.append(lambda: r)").replace("r + 1", "r + 2").replace("out = []", "out = [0]")
        loops = {(n.side, n.row) for n in BP.file_requirements("m.py", base.encode(), head.encode(), (False, set()))
                 if n.kind == "loop"}
        self.assertEqual(loops, {("BASE", 4), ("HEAD", 4), ("BASE", 5), ("HEAD", 5)})
        # a loop longer than one byte's jump: its back-edges go to the EXTENDED_ARG before the FOR_ITER
        long = "def f(v):\n    out = []\n    for r in v:\n" + "".join(f"        x{i} = r + {i}\n" for i in range(150)) \
            + "        out.append(r)\n    return out\n"
        src = BP._source(long, "m.py")
        loop, = [lp for lp in src.loops if isinstance(lp.node, ast.For)]
        self.assertTrue(loop.backs)
        self.assertEqual([p[1] for p in BP.watched(src, [154]).probes][:1], [154])
        need = BP.Need(0, "m.py", 4, "", "loop", frozenset({(1, 4, 12)}))
        self.assertFalse(BP.satisfied(need, BP._ran({"loops": {"m.py": [[1, 4, 13]]}})))
        self.assertTrue(BP.satisfied(need, BP._ran({"loops": {"m.py": [[1, 4, 12]]}})))

    def test_a_statement_keeps_its_tries(self):
        """TJ-4632: a statement or call keeps the tries, finallys and withs
        around it: moved out of its try (Fable's `_PL.compare`), into one, an
        else made a finally around it, refused whatever ran; a helper called
        inside the try, or a copy added while the original stays in its try
        (new code, which the line rules hold), passes."""
        def moved(base, head):
            d = BP.diff_rows(base, head)
            return sorted((n.row, n.why.split(" (")[0]) for n in BP.context_requirements("m.py", base, head, d))
        tried = "def f(v):\n    try:\n        a = int(v)\n        b = 1 // a\n    except ZeroDivisionError:\n        return 0\n    return b\n"
        out = tried.replace("        b = 1 // a\n", "").replace("    return b\n", "    b = 1 // a\n    return b\n")
        self.assertEqual(moved(tried, out), [(6, "`b = 1 // a` moved into or out of a try, a finally or a with")])
        self.assertEqual(moved(out, tried), [(4, "`b = 1 // a` moved into or out of a try, a finally or a with")])
        other = tried.replace("ZeroDivisionError", "ValueError")
        self.assertEqual(sorted({r for r, _w in moved(tried, other)}), [3, 4, 6])     # what the handler sees too
        orelse = "def f(v):\n    try:\n        a = int(v)\n    except ValueError:\n        a = 0\n    else:\n        print(a)\n"
        # the try now has a finally: what its body and handler raise passes through it too
        self.assertEqual(sorted({r for r, _w in moved(orelse, orelse.replace("else:", "finally:"))}), [3, 5, 7])
        within = "def f(v):\n    with open(v) as fh:\n        x = fh.read()\n    return x\n"
        self.assertEqual(moved(within, within.replace("        x = fh.read()\n", "        pass\n    x = fh.read()\n")),
                         [(4, "`fh.read()` moved into or out of a try, a finally or a with"),
                          (4, "`x = fh.read()` moved into or out of a try, a finally or a with")])
        helper = "def g(v):\n    return int(v)\n\n\n" + tried.replace("int(v)", "g(v)")
        self.assertEqual(moved(tried, helper), [])
        copied = tried.replace("    return b\n", "    b = 1 // a\n    return b\n")
        self.assertEqual(moved(tried, copied), [])      # its original kept in the try: new code, the line rules'
        replaced = tried.replace("    return b\n", "    c = 1 // a\n    return b\n").replace("        b = 1 // a\n", "        pass\n")
        self.assertEqual(moved(tried, replaced + "\n\ndef g(a):\n    b = 1 // a\n    return b\n"),
                         [(12, "`b = 1 // a` moved into or out of a try, a finally or a with")])
        # a call moved into a try: said at the call, not on each line of what it calls
        show = "def show(c):\n    print(c)\n    return c\n\n\ndef f(v):\n    c = int(v)\n    show(c)\n"
        into = show.replace("    show(c)\n", "    try:\n        show(c)\n    except ValueError:\n        pass\n")
        self.assertEqual(sorted(set(moved(show, into))), [(9, "`show(c)` moved into or out of a try, a finally or a with")])
        twice = "def f(v):\n    x = str(v)\n    return x\n"
        self.assertEqual(moved(twice, twice.replace("    return x\n", "    y = str(v)\n    return x + y\n")), [])

    def test_a_changed_text_needs_to_be_shown(self):
        """TJ-4633: each fragment of a changed string (split at its format
        fields; four characters not blank or more) a recording must show:
        not a key, a test's operand, a codec, a format's spec, an
        annotation, a docstring no code reads; a data file's line by its
        own fragments."""
        def texts(line, docs=(False, set())):
            text = f"def f(d, x: 'Annot'):\n    {line}\n"
            return sorted(f for n in BP.text_requirements("m.py", text, [2], docs) for f in n.keys)
        self.assertEqual(texts('print("Usage: tjs  thing")'), ["Usage: tjs thing"])
        self.assertEqual(texts('print(f"{x:,.2f} in total, {x} left")'), ["in total,", "left"])
        self.assertEqual(texts('print("{} rows, %s of %(n)s files" % x)'), ["files", "rows,"])
        self.assertEqual(texts('print("abc", "a b c d")'), ["a b c d"])
        for line in ('return d["a key"]', 'return d.get("a key")', 'return x == "a value"',
                     'return getattr(d, "an_attr")', 'return x.encode(encoding="latin-1")', '"""a doc string"""'):
            with self.subTest(line=line):
                self.assertEqual(texts(line), [])
        self.assertEqual(texts('"""a doc string"""', docs=(True, set())), ["a doc string"])
        data = BP.data_requirements("x.toml", [2], [], "a = 1\nname = \"the long name\"\n")
        self.assertEqual(sorted((n.kind, tuple(sorted(n.keys))) for n in data),
                         [("opened", ()), ("text", ("name", "the long name"))])
        shown = frozenset({"name", "the long name"})
        need = next(n for n in data if n.kind == "text")
        self.assertTrue(BP.satisfied(need, BP._ran({}), shown))
        self.assertFalse(BP.satisfied(need, BP._ran({})))

    def test_a_line_with_no_instruction_of_its_own(self):
        """TJ-4634: a line inside a statement counts as run only through
        the part of it on that line, and every conditional around that part
        taken both ways; a clause keyword line by the first statement of its
        body; a lambda on a line, by its call."""
        base = 'def f(v):\n    m = ("fine" if v else\n         "see "\n         "the rest")\n    return m\n'
        kinds = {(n.side, n.row, n.kind) for n in BP.file_requirements(
            "m.py", base.encode(), base.replace("the rest", "THE REST").encode(), (False, set()))}
        self.assertTrue({("HEAD", 4, "rows"), ("HEAD", 4, "branch"), ("BASE", 4, "rows"), ("BASE", 4, "branch"),
                         ("HEAD", 4, "text")} <= kinds, kinds)
        lam = "H = {'a': lambda: 'one', 'b': lambda: 'two'}\n"
        need = [(n.side, n.kind, tuple(n.keys)) for n in BP.file_requirements(
            "m.py", lam.encode(), lam.replace("two", "TWO").encode(), (False, set())) if n.kind == "line"]
        self.assertEqual(sorted(need), [(side, "line", ((idx, 1),)) for side in BP.SIDES for idx in (0, 1, 2)])
        fin = "def f():\n    try:\n        x = 1\n    finally:\n        x = 2\n    return x\n"
        clause = {(n.side, n.kind, n.why) for n in BP.file_requirements(
            "m.py", fin.encode(), fin.replace("finally", "except Exception").encode(), (False, set())) if n.row == 4}
        self.assertEqual(clause, {("BASE", "rows", "a changed clause line, the first statement of its body never run"),
                                  ("HEAD", "line", "a changed line"),           # the except's match is on it
                                  ("HEAD", "branch", "a changed branch not taken both ways")})

    def test_an_entry_names_one_line(self):
        """TJ-4636: an entry excuses the one line it names (HEAD's, or with
        `BASE ` BASE's), while that line holds its text: another line of
        the same text is not excused."""
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            (tree / BP.UNREACHABLE).parent.mkdir(parents=True)
            (tree / BP.UNREACHABLE).write_text(f"{BP.SRC}/m.py:3 :: return :: made up\n"
                                               f"BASE {BP.SRC}/m.py:2 :: return 1 :: made up\n"
                                               f"{BP.SRC}/m.py:2 :: return :: made up\n")
            files = {"HEAD": {"m.py": b"def f():\n    x = 1\n    return\n    return\n"},
                     "BASE": {"m.py": b"def f():\n    return 1\n"}}
            self.assertEqual(BP.read_unreachable(tree, files),
                             ({("HEAD", "m.py", 3), ("BASE", "m.py", 2)},
                              [f"{BP.UNREACHABLE}:3: `return` is not line 2 of {BP.SRC}/m.py"]))

    def test_a_file_not_python_needs_its_open(self):
        d = BP.diff_rows("a = 1\nb = 2\n", "a = 1\nb = 3\n", python=False)
        self.assertEqual((d.head, d.base), ([2], [2]))
        need, = BP.data_requirements("x.toml", d.head, d.gaps, "a = 1\nb = 3\n")
        self.assertEqual((need.row, need.kind, need.text), (2, "opened", "b = 3"))
        self.assertTrue(BP.satisfied(need, BP._ran({"opened": ["x.toml"]})))
        self.assertFalse(BP.satisfied(need, BP._ran({"lines": {"x.toml": [[0, 2]]}})))

    def test_the_trees_and_the_tmpdir(self):
        """A tree is a folder holding src/taxjson or a commit; --compare
        needs --trees; two recordings under TMPDIRs of other lengths are
        refused (`tmpdir`, checked)."""
        with redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as e:
            BP.main(["--compare", "a", "b"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("--compare needs --trees BASE HEAD", said.getvalue())
        for argv in (["--list-commands", "--trees", "a", "b"], ["--list-commands", "--reach-base", "a"],
                     ["--list-commands", "--reach-head", "a"], ["--record", "x", "--reach-base", "a", "--reach-head", "b"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                BP.main(argv)
            self.assertEqual(e.exception.code, 2)
        with mock.patch.object(BP.tempfile, "gettempdir", lambda: "/made/up/tmp"):
            self.assertEqual(BP.tmpdir_shape(), [12, len(os.path.realpath("/made/up/tmp"))])
        self.assertIn("tmpdir", BP._CHECKED)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(SystemExit, "no src/taxjson in it"):
                BP.src_files(d)
            (Path(d) / BP.SRC / "__pycache__").mkdir(parents=True)
            (Path(d) / BP.SRC / "__pycache__" / "a.pyc").write_bytes(b"\0")
            (Path(d) / BP.SRC / "a.py").write_text("x = 1\n")
            self.assertEqual(BP.src_files(d), {"a.py": b"x = 1\n"})
        with self.assertRaisesRegex(SystemExit, "neither a folder nor a commit"):
            BP.src_files("no-such-commit-zz")
        here = subprocess.run(["git", "-C", str(BP.REPO), "ls-tree", "-r", "--name-only", "HEAD", BP.SRC],
                              capture_output=True, text=True, check=False)
        if here.returncode == 0:      # (a checkout without git: an archive)
            self.assertEqual(set(BP.src_files("HEAD")),
                             {p[len(BP.SRC) + 1:] for p in here.stdout.split()})

    def test_the_other_taxjson_record_is_taxjsons_own(self):
        """The record `-C other cash` reads (TJ-4624): its files table is
        what taxjson's own fingerprint makes of the files the line appends,
        so the refusal is of other code, not of changed inputs."""
        from taxjson.lib.checklist import FINGERPRINT_VERSION, input_fingerprint
        from taxjson.pipeline.config import load_config
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d)
            line = BP.parse_command("-C other cash # exit 1" + BP._OTHER)
            BP.apply_edits(copy, ".", line.edits, AT)
            root = copy / "other"
            record = json.loads((root / "work" / ".inputs_fingerprint.json").read_text())
            self.assertEqual(input_fingerprint(root, load_config(root)), record["files"])
        self.assertEqual((record["code"], record["version"]), ("another taxjson", FINGERPRINT_VERSION))


@needs_namespace
class TestTracingMovesNoByte(unittest.TestCase):
    """Fable's check, kept (TJ-4629 to TJ-4631): real commands recorded with
    the reach trace on, against a made-up other tree in which every module
    differs (each traced: its lines and code entered) and those they print
    through are empty (every line of them changed: each branch, loop and
    default watched), are byte for byte the recording made with it off;
    and the trace saw them run."""

    def test_equal(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tmp = Path(d.name)
        other = tmp / "other" / BP.SRC
        shutil.copytree(BP.REPO / BP.SRC, other, ignore=shutil.ignore_patterns("__pycache__"))
        for p in other.rglob("*.py"):
            p.write_text(p.read_text(encoding="utf-8") + "# made up\n", encoding="utf-8")
        for rel in ("lib/out.py", "lib/report_model.py", "pipeline/sanity.py"):
            (other / rel).write_text("")
        commands = {"demo": ["sanity", "years --json"]}
        passes = [p for p in BP.PASSES if p.name in ("w0", "tty")]
        BP.record(tmp / "a", ["demo"], commands, jobs=2, passes=passes, instants=(AT,))
        BP.record(tmp / "b", ["demo"], commands, jobs=2, passes=passes, instants=(AT,), reach_base=str(tmp / "other"))
        code, lines = BP.compare(tmp / "a", tmp / "b")
        self.assertEqual(code, 3, lines)
        self.assertTrue(lines[0].startswith("byte proof: equal"), lines)
        reach = json.loads((tmp / "b" / BP.PROOF).read_text())["reach"]
        self.assertEqual(reach["side"], "HEAD")
        self.assertEqual(set(reach["traced"]), {r for r in BP.src_files(BP.REPO) if r.endswith(".py")})
        for kind in ("lines", "entered", "branches", "loops"):
            with self.subTest(kind=kind):
                self.assertTrue(reach[kind].get("lib/out.py"), kind)
        self.assertTrue(reach["lines"].get("pipeline/sanity.py"))
        self.assertIsNone(json.loads((tmp / "a" / BP.PROOF).read_text())["reach"])


@needs_namespace
class TestOneTreeTwice(unittest.TestCase):
    """Real commands, one tree recorded twice at width 0 and on a terminal
    at both instants, with no mask but the folders: the demo book's run
    (its reports among `_run`'s files), `snapshot` (its folder named for
    the time), `years` and `years --json` (each year's last run, a file's
    time), a stale-books refusal after an edit, close-year's lock answered
    yes, the evidence book's `align --from 2023` (the folder above the
    project), and an engine book's run: equal, and said to be of one tree."""

    def test_equal(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tmp = Path(d.name)
        edit = "sum # edit: append ../inputs/margin/ib_scenarios_demo.csv"
        commands = {"demo": ["snapshot", "years", "years --json", edit, "close-year # stdin: y"],
                    "evidence": ["align --from 2023 # exit 2"], "fuzz-straddle": []}
        passes = [p for p in BP.PASSES if p.name in ("w0", "tty")]
        for name in ("a", "b"):
            BP.record(tmp / name, list(commands), commands, jobs=3, passes=passes)
        code, lines = BP.compare(tmp / "a", tmp / "b")
        self.assertEqual(code, 3, lines)
        self.assertTrue(lines[0].startswith("byte proof: equal"), lines)
        demo, evidence = tmp / "a" / "demo", tmp / "a" / "evidence"
        self.assertIn("<top>/2023", (evidence / "align_from_2023" / LABEL / "w0.stderr").read_text())
        self.assertIn("added dir 0700 p/snapshot/2024.20261118-213000",
                      (demo / "snapshot" / LABEL / "w0.meta").read_text())
        self.assertIn("2026-11-17 09:30", (demo / "years" / LABEL / "w0.stdout").read_text())
        self.assertIn('"last_run": "2026-03-01T09:30:00"',
                      (demo / "years_json" / MARCH_LABEL / "w0.stdout").read_text())
        self.assertIn("the books in work/ are older than the inputs (changed: inputs/margin/ib_scenarios_demo.csv)",
                      (demo / BP.command_name(edit) / LABEL / "w0.stderr").read_text())
        locked = demo / "close-year~stdin-y" / LABEL
        self.assertEqual((locked / "passes").read_text(), "w0\ntty\n")
        self.assertIn("Lock:                filed/2024.json", (locked / "tty.out").read_text())
        self.assertIn("margin.sum", {p.name for p in (demo / BP.RUN / LABEL / "w0.files").rglob("*.sum")})
        # the synthetic rates' HOME: the run reaches its last stage
        self.assertIn("==> Done. Reports are in reports/", (demo / BP.RUN / MARCH_LABEL / "w0.stdout").read_text())
        self.assertTrue((demo / BP.RUN / LABEL / "tty.out").read_bytes())
        engine = tmp / "a" / "fuzz-straddle" / BP.RUN / LABEL / "w0.files" / "p"
        self.assertEqual(sorted(p.name for p in engine.iterdir()), ["canada.json", "usa.json"])
        proof = json.loads((tmp / "a" / BP.PROOF).read_text())
        self.assertEqual(proof["packages"], BP.packages())


if __name__ == "__main__":
    unittest.main()

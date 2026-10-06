"""The house output style (docs/output-style.md): lib/out.py, and the two
commands converted first — `taxjson elect` (its listings, --set, the
election prompt, the run's pending block) and `taxjson wash-sales` (the
table and --explain).

Layout is pinned with out.lint at width 100: no prose line over the
width (table rows and commands to copy are exempt), one blank line
between sections, no retired prefix. Every fixture is synthetic."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib import out

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = str(REPO_ROOT / "src")


def _env(**extra):
    e = dict(os.environ)
    e.pop("TAXJSON_WIDTH", None)
    e["TAXJSON_OFFLINE"] = "1"
    # The worktree's code, whatever the caller's PYTHONPATH says.
    e["PYTHONPATH"] = SRC + os.pathsep + e.get("PYTHONPATH", "")
    e.update(extra)
    return e


def _cli(root, *args, **env):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        env=_env(**env), stdin=subprocess.DEVNULL, timeout=600)


def _flat(text):
    return " ".join(text.split())


class _NoWidthEnv(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_WIDTH", None)


# ------------------------------------------------------------- lib/out
class TestWidth(_NoWidthEnv):
    def test_pipe_is_120_and_env_overrides(self):
        self.assertEqual(out.width(io.StringIO()), 120)
        self.assertEqual(out.WIDTH, 120)
        os.environ["TAXJSON_WIDTH"] = "72"
        self.assertEqual(out.width(io.StringIO()), 72)
        os.environ["TAXJSON_WIDTH"] = "0"
        self.assertEqual(out.width(io.StringIO()), 0)
        os.environ["TAXJSON_WIDTH"] = "10"
        self.assertEqual(out.width(io.StringIO()), out.MIN_WIDTH)

    def test_terminal_is_its_full_width_capped_at_160(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        for cols, want in ((200, 160), (160, 160), (140, 140),
                           (70, 70), (20, out.MIN_WIDTH)):
            with self.subTest(cols=cols), mock.patch(
                    "shutil.get_terminal_size",
                    return_value=os.terminal_size((cols, 50))):
                self.assertEqual(out.width(Tty()), want)
        # TAXJSON_WIDTH wins over the terminal; 0 never wraps.
        with mock.patch("shutil.get_terminal_size",
                        return_value=os.terminal_size((200, 50))):
            os.environ["TAXJSON_WIDTH"] = "90"
            self.assertEqual(out.width(Tty()), 90)
            os.environ["TAXJSON_WIDTH"] = "0"
            self.assertEqual(out.width(Tty()), 0)
            del os.environ["TAXJSON_WIDTH"]
            # Captured for a program: 0 whatever the terminal says.
            with out.unwrapped():
                self.assertEqual(out.width(Tty()), 0)


class TestWrapAndMessages(_NoWidthEnv):
    def test_code_spans_and_ids_never_break(self):
        text = ("word " * 15 + "`taxjson elect margin --set "
                "20240603-abc-def-1234=rollover` and CA-SL-08-style ids")
        lines = out.wrap(text, 40)
        self.assertTrue(any("`taxjson elect margin --set "
                            "20240603-abc-def-1234=rollover`" in ln
                            for ln in lines), lines)
        self.assertTrue(any("CA-SL-08-style" in ln for ln in lines))

    def test_width_zero_is_one_line(self):
        self.assertEqual(out.wrap("a " * 200, 0), ["a " * 199 + "a"])

    def test_message_headline_and_details(self):
        lines = out.message("warning", "short headline", prog="taxjson x",
                            details=["why " * 40, "- an item " + "word " * 20],
                            width_=60)
        # Shown to a person: the label starts the line, no program name.
        self.assertEqual(lines[0], "Warning: short headline")
        # Every later line is flush-left (an item and its wrapped lines
        # too: no indentation in a message).
        self.assertTrue(all(ln and not ln[0].isspace() for ln in lines),
                        lines)
        self.assertTrue(all(len(ln) <= 60 for ln in lines))
        item = [ln for ln in lines if ln.startswith("- ")]
        self.assertEqual(len(item), 1)
        cont = lines[lines.index(item[0]) + 1]
        self.assertTrue(cont.startswith("word"), cont)
        self.assertEqual(out.console_lint("\n".join(lines), 60), [])

    def test_captured_message_keeps_the_gnu_bytes(self):
        # Width 0 (a .diag, the checklist's read): `<prog>: <kind>:`.
        for kind, want in (("note", "taxjson x: note: h"),
                           ("warning", "taxjson x: warning: h"),
                           ("attention", "taxjson x: warning: ATTENTION: h"),
                           ("error", "taxjson x: error: h")):
            self.assertEqual(out.message(kind, "h", prog="taxjson x",
                                         width_=0), [want])
        with out.unwrapped():
            self.assertEqual(out.message("note", "h")[0], "note: h")

    def test_person_labels(self):
        for kind, want in (("note", "Info: h"), ("warning", "Warning: h"),
                           ("attention", "Warning: h"),
                           ("error", "Error: h")):
            self.assertEqual(out.message(kind, "h", prog="taxjson-x",
                                         width_=100), [want])
            self.assertEqual(out.label(kind, 100) + "h", want)

    def test_attention_keeps_the_run_marker(self):
        # Shown: a Warning, the topic capitalised; the ATTENTION word is
        # the captured form's.
        line = out.message("attention", "short: ABC.TO goes short")[0]
        self.assertEqual(line, "Warning: Short position: ABC.TO goes short")
        with out.unwrapped():
            line = out.message("attention", "short: ABC.TO goes short")[0]
        self.assertTrue(line.startswith("warning: ATTENTION: short: "))

    def test_relabel(self):
        R = out.relabel
        self.assertEqual(R("warning: ATTENTION: short: X"),
                         "Warning: Short position: X")
        self.assertEqual(R("warning: ATTENTION: x.csv: no Cash Report"),
                         "Warning: x.csv: no Cash Report")
        self.assertEqual(R("  note: x"), "  Info: x")
        self.assertEqual(R("NOTE: x"), "Info: x")
        self.assertEqual(R("taxjson-gains: error: x"),
                         "Error: taxjson-gains: x")
        self.assertEqual(R("taxjson-gains: error: x", source=False),
                         "Error: x")
        self.assertEqual(R("taxjson run: warning: x", source=False),
                         "Warning: x")
        for same in ("margin: note: x", "taxjson.toml: warning: x",
                     "Warning: ok", "no label here"):
            self.assertEqual(R(same), same)

    def test_lint_flags_captured_labels_in_person_output(self):
        for bad in ("note: x", "  warning: x", "error: x", "NOTE: x",
                    "taxjson x: warning: x", "taxjson-x: Error: x"):
            self.assertTrue(out.lint(bad), bad)
        for ok in ("Info: x", "  Warning: ATTENTION: short: x",
                   "Error: taxjson-gains: x"):
            self.assertEqual(out.lint(ok), [], ok)

    def test_exit_text(self):
        with out.unwrapped():
            self.assertEqual(out.exit_text("taxjson sum: no gains"),
                             "taxjson sum: no gains")
        self.assertEqual(out.exit_text("taxjson sum: no gains"),
                         "Error: no gains")
        self.assertEqual(out.exit_text("taxjson-x: error: bad"),
                         "Error: bad")
        self.assertEqual(out.exit_text(
            "taxjson run --strict: 1 year drifted"),
            "Error: 1 year drifted")

    def test_fail_exit_1_carries_the_text(self):
        with self.assertRaises(SystemExit) as cm:
            out.fail("it broke", prog="taxjson x", details=["fix it"])
        # Shown to a person: the detail flush-left.
        self.assertEqual(str(cm.exception), "Error: it broke\nfix it")
        with out.unwrapped(), self.assertRaises(SystemExit) as cm:
            out.fail("it broke", prog="taxjson x", details=["fix it"])
        self.assertEqual(str(cm.exception),
                         "taxjson x: error: it broke\n  fix it")


class TestBlankLineAfterMultiLineMessages(_NoWidthEnv):
    """A message (or step) of more than one line is followed by exactly
    one blank line — owed, printed before the next text to the same
    destination, never at the end; a one-line message owes nothing."""

    def setUp(self):
        super().setUp()
        out._OWED.clear()
        self.addCleanup(out._OWED.clear)

    LONG = "word " * 40

    def test_one_line_messages_have_no_blank_line(self):
        buf = io.StringIO()
        out.note("one", file=buf)
        out.warn("two", file=buf)
        self.assertEqual(buf.getvalue(), "Info: one\nWarning: two\n")

    def test_multi_line_message_then_one_blank_line_never_at_the_end(self):
        buf = io.StringIO()
        out.warn(self.LONG, file=buf)
        text = buf.getvalue()
        self.assertFalse(text.endswith("\n\n"), text)    # owed, not printed
        out.note("next", file=buf)
        text = buf.getvalue()
        self.assertIn("\n\nInfo: next\n", text)
        self.assertNotIn("\n\n\n", text)
        self.assertEqual(out.console_lint(text), [])
        # Details: headline and detail lines flush-left, one blank after.
        buf = io.StringIO()
        out.error("head", details=["why", "- an item"], file=buf)
        out.error("again", file=buf)
        self.assertEqual(buf.getvalue(),
                         "Error: head\nwhy\n- an item\n\nError: again\n")

    def test_captured_owes_nothing(self):
        os.environ["TAXJSON_WIDTH"] = "0"
        buf = io.StringIO()
        out.warn("w", details=["d " * 80], file=buf)
        out.note("n", file=buf)
        self.assertEqual(buf.getvalue(),
                         "warning: w\n  " + ("d " * 80).strip()
                         + "\nnote: n\n")
        self.assertEqual(out._OWED, {})

    def test_owed_per_destination(self):
        # stdout and stderr to different places (`2>err.txt`): each
        # keeps its own messages apart, neither gets the other's blank.
        a, b = io.StringIO(), io.StringIO()
        out.warn(self.LONG, file=a)
        out.note("on b", file=b)
        out.note("on a", file=a)
        self.assertEqual(b.getvalue(), "Info: on b\n")
        self.assertIn("\n\nInfo: on a\n", a.getvalue())

    def test_one_destination_two_streams(self):
        # `2>&1`, a terminal: two streams, one pipe — the blank line is
        # printed before the next text whichever stream says it.
        r, w = os.pipe()
        w2 = os.dup(w)
        f1 = os.fdopen(w, "w")
        f2 = os.fdopen(w2, "w")
        try:
            out.show(["Warning: x", "more"], f1)
            f1.flush()
            out.show(["==> step"], f2)
            f2.flush()
        finally:
            f1.close()
            f2.close()
        with os.fdopen(r) as rf:
            self.assertEqual(rf.read(), "Warning: x\nmore\n\n==> step\n")

    def test_continuation_joins_the_entry(self):
        buf = io.StringIO()
        out.show(["Warning: head"], buf)
        out.show(["detail"], buf, cont=True)
        out.show(["Info: next"], buf)
        self.assertEqual(buf.getvalue(),
                         "Warning: head\ndetail\n\nInfo: next\n")

    def test_settling_streams_cover_a_raw_print(self):
        outb, errb = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(outb), \
                contextlib.redirect_stderr(errb), out.settling_streams():
            out.warn(self.LONG, file=sys.stdout)
            print("REPORT TITLE")              # a raw print
            out.warn(self.LONG, file=sys.stdout)
            print()                            # its own blank line
            print("next")
        text = outb.getvalue()
        self.assertIn("\n\nREPORT TITLE\n", text)
        self.assertNotIn("\n\n\n", text)
        self.assertTrue(text.endswith("\n\nnext\n"), text)

    def test_run_top_level_settles_before_an_exit_text(self):
        from taxjson.lib import cli_diag
        errb = io.StringIO()

        def main():
            out.warn(self.LONG, file=sys.stderr)
            raise SystemExit("Error: stop")
        with contextlib.redirect_stderr(errb), \
                self.assertRaises(SystemExit):
            cli_diag.run_top_level("taxjson-x", main)
        # The interpreter prints the exit text next: the blank is there.
        self.assertTrue(errb.getvalue().endswith("\n\n"), errb.getvalue())

    def test_join_blocks(self):
        self.assertEqual(out.join_blocks([["==> a"], ["Info: b", "c"],
                                          ["Info: d"], ["Info: e", "f"]]),
                         ["==> a", "Info: b", "c", "", "Info: d",
                          "Info: e", "f"])

    def test_doc_message(self):
        d = out.Doc("TITLE", width_=60)
        d.message("warning", self.LONG).line("next").message("note", "x")
        lines = d.lines()
        self.assertEqual(lines[-3:], ["", "next", "Info: x"])
        self.assertEqual(out.lint(d.text(), 60), [])


class TestTables(_NoWidthEnv):
    H = ["ACCOUNT", "DATE", "SYMBOL", "QTY", "PROCEEDS", "COST", "GAIN"]
    ROWS = [["margin", "2025-03-10", "XEI.TO", "100", "800.00",
             "1,000.00", "-200.00"]]

    def test_numbers_right_align(self):
        lines = out.fit_table(self.H, self.ROWS + [
            ["tfsa", "2025-03-11", "XIC.TO", "5", "80.00", "100.00",
             "-20.00"]])
        self.assertTrue(lines[1].startswith("---"))
        # GAIN is the last column: right-aligned, both rows end together.
        self.assertEqual(len(lines[2]), len(lines[3]))
        self.assertTrue(lines[3].endswith("  -20.00"))

    def test_drop_then_per_record(self):
        full = out.fit_table(self.H, self.ROWS, width_=200)
        self.assertIn("PROCEEDS", full[0])
        dropped = out.fit_table(self.H, self.ROWS, drop=(5, 4),
                                width_=len(full[0]) - 5)
        self.assertNotIn("COST", dropped[0])
        self.assertTrue(all(len(ln) <= len(full[0]) - 5 for ln in dropped))
        rec = out.fit_table(self.H, self.ROWS, drop=(5,), width_=40,
                            key=(2, 1))
        self.assertEqual(rec[0], "XEI.TO  2025-03-10")
        self.assertTrue(all(len(ln) <= 40 for ln in rec), rec)
        self.assertIn("cost 1,000.00", _flat("\n".join(rec)))

    def test_doc_spacing(self):
        d = out.Doc("TITLE — x", width_=100)
        d.blank().blank().section("ONE").blank().blank().item("a")
        d.section("TWO").para("b").blank()
        self.assertEqual(d.lines(), ["TITLE — x", "", "ONE", "", "- a",
                                     "", "TWO", "b"])
        self.assertEqual(out.lint(d.text()), [])

    def test_lint_finds_problems(self):
        bad = "\nNOTE: x\n\n\n" + "y " * 70 + "\n"
        probs = " | ".join(out.lint(bad))
        for want in ("leading blank", "retired prefix", "two blank",
                     "columns"):
            self.assertIn(want, probs)
        self.assertEqual(out.lint("A  B  C  " + "x" * 120), [])


# ---------------------------------------------------------------- elect
_CA = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"

[accounts.tfsa]
type = "sheltered"
"""

_EV = "20250601-parn-spnc-0001"
_LONG = ("Defer the income: part of the parent's cost basis moves to the "
         "spun-off shares, no tax this year. Only valid if the spinoff is on "
         "the eligibility list AND you file the election with your return; "
         "you supply the cost to allocate. " * 2)


def _pending_doc(account="margin"):
    return {"schema_version": 1, "accounts": {account: {"pending": [{
        "event_id": _EV,
        "summary": "2025-06-01 spinoff: PARN.US → SPNC.US (1-for-10)",
        "options": [
            {"election": "taxable_deemed_dividend",
             "description": "Report the received shares as a dividend at "
                            "FMV. " * 4, "hints": []},
            {"election": "rollover_s_86_1", "description": _LONG,
             "hints": [{"key": "allocated_acb_cad",
                        "prompt": "Cost (in CAD) moved from the parent "
                                  "to the spun-off shares. " * 3}]},
            {"election": "ignore", "description": "Skip this event.",
             "hints": []}]}]}}}


def _elect_project(tmp, manifest=None, pending=None):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_CA)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "work").mkdir()
    if manifest is not None:
        (root / "inputs" / "margin" / "manifest.json").write_text(
            json.dumps({"elections": manifest}))
    if pending is not None:
        (root / "work" / "pending_elections.json").write_text(
            json.dumps(pending))
    return root


def _elect(root, **kw):
    from taxjson.bin.taxjson_run import cmd_elect
    ns = dict(dir=str(root), account=None, redo=False, reset=False,
              event=None, set=None, hint=None, pending=False, json=False)
    ns.update(kw)
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        cmd_elect(argparse.Namespace(**ns))
    return o.getvalue(), e.getvalue()


class TestElectStyle(_NoWidthEnv):
    def test_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _elect_project(tmp, manifest={
                _EV: {"election": "taxable_deemed_dividend",
                      "summary": "2025-06-01 spinoff: PARN.US → SPNC.US " * 3,
                      "hints": {"fmv_per_share": 12.5, "x": 400.0},
                      "notes": "set via elect --set"},
                "20250102-abc-xyz-0001": {"election": "taxable_dispostion"}})
            text, _ = _elect(root)
        self.assertEqual(out.lint(text), [], text)
        lines = text.splitlines()
        self.assertEqual(lines[:3], ["CORPORATE-ACTION ELECTIONS", "",
                                     "margin — inputs/margin/manifest.json"])
        self.assertIn(f"- {_EV}: taxable_deemed_dividend", lines)
        self.assertIn("  hints:  fmv_per_share=12.5, x=400", lines)
        bad = [ln for ln in lines if "taxable_dispostion" in ln]
        self.assertIn("UNKNOWN election", bad[0])
        self.assertIn("No elections recorded: tfsa.", lines)
        self.assertTrue(lines[-1].startswith("Redo one: "))

    def test_pending_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _elect_project(tmp, pending=_pending_doc())
            text, _ = _elect(root, pending=True)
        self.assertEqual(out.lint(text, allow=("taxjson elect ",)), [],
                         text)
        lines = text.splitlines()
        self.assertEqual(lines[0], "PENDING ELECTIONS — 1 event")
        self.assertIn(f"margin: {_EV}", lines)
        self.assertIn("  2. rollover_s_86_1", lines)
        sets = [ln for ln in lines if ln.lstrip().startswith("set:")]
        self.assertEqual(len(sets), 3)
        self.assertIn(f"taxjson elect margin --set {_EV}=rollover_s_86_1 "
                      f"--hint allocated_acb_cad=...", sets[1])
        # The checklist's elections step shows the last line.
        self.assertTrue(lines[-1].startswith("1 election pending: "),
                        lines[-1])
        self.assertLessEqual(len(lines[-1]), 100)

    def test_pending_after_set_says_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _elect_project(tmp, pending=_pending_doc(), manifest={
                _EV: {"election": "taxable_deemed_dividend"}})
            text, _ = _elect(root, pending=True)
        self.assertIn("already elected: taxable_deemed_dividend", text)
        self.assertIn("run `taxjson run`", text.splitlines()[-1])

    def test_set_errors_are_headline_and_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _elect_project(tmp, pending=_pending_doc())
            with self.assertRaises(SystemExit) as cm:
                _elect(root, account="margin",
                       set=[f"{_EV}=rollover_s_86_1"])
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("Error: "
                                       "rollover_s_86_1 needs "
                                       "allocated_acb_cad\nAdd "), msg)
        self.assertEqual(out.lint(msg), [], msg)

    def test_set_confirmation_and_zero_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _elect_project(tmp, pending=_pending_doc())
            o, e = _elect(root, account="margin",
                          set=[f"{_EV}=rollover_s_86_1"],
                          hint=["allocated_acb_cad=0"])
        self.assertEqual(o.splitlines()[0],
                         f"Election saved: {_EV} = rollover_s_86_1")
        self.assertIn("  file:   inputs/margin/manifest.json", o)
        self.assertTrue(e.startswith("Warning: "
                                     "allocated_acb_cad=0 moves NO cost"))
        self.assertEqual(out.lint(o), [])
        self.assertEqual(out.lint(e), [])


class TestElectionPrompt(_NoWidthEnv):
    def test_prompt_is_wrapped(self):
        from taxjson.bin.taxjson_corp_actions import _prompt_election
        from taxjson.lib.corp_actions import CorporateAction
        ev = CorporateAction(
            date="2025-06-01", time="20:25:00", action_type="spinoff",
            source_symbol="PARN.US", source_isin="US0000000401",
            target_symbol="SPNC.US", target_isin="US0000000402",
            ratio_new=1.0, ratio_old=10.0, qty_disposed=0.0,
            qty_received=10.0, fmv=250.0, currency="USD",
            target_currency="USD", account="margin",
            raw_descriptions=["PARN Spinoff 1 for 10"], event_id=_EV)
        err = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("2\n400\nmy note\n")), \
                contextlib.redirect_stderr(err):
            rec = _prompt_election(ev, "canada")
        self.assertEqual((rec.election, rec.hints),
                         ("rollover_s_86_1", {"allocated_acb_cad": 400.0}))
        text = err.getvalue()
        self.assertIn(f"EVENT {_EV}", text)
        self.assertIn("  [2] rollover_s_86_1", text)
        # Prompts (no newline after them) aside, every line fits.
        shown = "\n".join(ln for ln in text.splitlines()
                          if not ln.rstrip().endswith(("]:", "=")))
        self.assertEqual([p for p in out.lint(shown.strip("\n"))
                          if "columns" in p], [], text)


# ----------------------------------------------------------- wash-sales
_WASH_TT = (
    "BUYSELL 2025-03-03 09:30:00 XEI.TO 100 CAD 10.0 1000.0 0\n"
    "BUYSELL 2025-03-10 09:30:00 XEI.TO -100 CAD 8.0 800.0 0\n"
    "BUYSELL 2025-03-12 09:30:00 XEI.TO 70 CAD 8.0 560.0 0\n")


class TestWashSalesStyle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._tmp.name)
        (root / "taxjson.toml").write_text(_CA)
        for acct, body in (("margin", _WASH_TT), ("tfsa", (
                "BUYSELL 2025-03-12 09:30:00 XEI.TO 30 CAD 8.0 240.0 0\n"))):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "rows.tt").write_text(body)
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_table(self):
        r = _cli(self.root, "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = r.stdout
        self.assertEqual(out.lint(text), [], text)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("SUPERFICIAL LOSSES — CAD, "
                                            "tax year 2025"))
        head = [ln for ln in lines if ln.startswith("ACCOUNT")][0]
        self.assertEqual(head.split(), ["ACCOUNT", "DATE", "SYMBOL", "QTY",
                                        "PROCEEDS", "COST", "GAIN",
                                        "DENIED", "ALLOWED"])
        self.assertIn("1 superficial loss(es); 200.00 CAD of losses denied "
                      "(60.00 permanently denied).", lines)
        self.assertIn("WHAT DENIED MEANS", lines)
        # One blank line before each section heading.
        i = lines.index("WHAT DENIED MEANS")
        self.assertEqual(lines[i - 1], "")
        self.assertNotEqual(lines[i - 2], "")

    def test_narrow_width_uses_records(self):
        r = _cli(self.root, "wash-sales", TAXJSON_WIDTH="50")
        self.assertEqual(out.lint(r.stdout, 50), [], r.stdout)
        self.assertIn("XEI.TO  2025-03-10  margin", r.stdout.splitlines())

    def test_explain(self):
        r = _cli(self.root, "wash-sales", "--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = r.stdout
        self.assertEqual(out.lint(text), [], text)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("SUPERFICIAL LOSSES — how each "
                                            "denial was computed"))
        self.assertIn("XEI.TO — 2025-03-10 — margin", lines)
        for sub in ("  Pool history (ACB trace)",
                    "  Superficial loss (ITA s.54)",
                    "  Superficial-loss window (±30 days, all accounts)"):
            self.assertIn(sub, lines)
            self.assertEqual(lines[lines.index(sub) - 1], "")
        self.assertNotIn("#", "".join(ln[:1] for ln in lines))
        self.assertIn("trigger, permanent [sheltered]", text)
        self.assertNotIn("$", text)


if __name__ == "__main__":
    unittest.main()

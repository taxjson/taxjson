"""lib/out's report renderer (PLAN step 15 commit 1): the Doc recorded as
ops, `exact`, the long form as data, the named parts, the JSON twin;
out.render and out.put. The reference is the code before the step:
pipeline/sanity's _json_out and _print_report_table as they were, the eager
Doc as it was, and the raw print() calls an exact Doc replaces. Compared as
bytes with every flush marked, at widths 0, 40 and 120, written plainly,
through the top of a process's settling streams with a blank line owed, and
to a captured stream that owes one (owing())."""
import contextlib
import datetime
import io
import json
import math
import os
import sys
import unittest
from decimal import Decimal
from pathlib import PurePosixPath
from unittest import mock

from taxjson.lib import out
from taxjson.lib.report_model import format_report_table
from taxjson.pipeline import sanity

WIDTHS = (0, 40, 120)
FLUSH = "<flush>"


class _Recorder(io.StringIO):
    """A stream whose value marks each flush() where it came."""

    def flush(self):
        self.write(FLUSH)


def captured(fn, width, settling=False):
    """stdout's text from fn() at TAXJSON_WIDTH `width`, its flushes
    marked; `settling`: inside settling_streams(), a two-line message
    shown to stdout first (so a blank line is owed at width > 0)."""
    buf = _Recorder()
    with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(width)}), \
            contextlib.redirect_stdout(buf):
        if settling:
            with out.settling_streams():
                out.show(["Info: before", "a second line"], file=sys.stdout)
                fn()
        else:
            fn()
    return buf.getvalue()


def owing(fn, width):
    """(stdout's text, whether a blank line is still owed) from fn() at
    TAXJSON_WIDTH `width` when stdout is captured into a StringIO with no
    settling_streams() (cli/estimate.py's captured sum, pipeline/run.py's
    captured stage output): a two-line message shown first owes a blank
    line at width > 0 that only an explicit settle() pays. Through
    settling_streams() the proxy pays it on the first write either way, so
    captured() cannot tell a raw print from one that settles."""
    buf = _Recorder()
    with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(width)}), \
            contextlib.redirect_stdout(buf):
        out.show(["Info: before", "a second line"], file=sys.stdout)
        fn()
        owed = out._take_owed(buf)
    return buf.getvalue(), owed


def old_json_out(doc):
    """pipeline/sanity._json_out before step 15, verbatim."""
    def _clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_clean(x) for x in v]
        return v

    print(json.dumps(_clean(doc), indent=2, sort_keys=True, default=str))


def old_print_report_table(rows, padding="   ", rule_before_last=False, **fit):
    """pipeline/sanity._print_report_table before step 15, verbatim."""
    for line in format_report_table(rows, padding=padding,
                                    rule_before_last=rule_before_last,
                                    **fit):
        print(line)


class OldDoc:
    """lib/out.Doc before step 15 (eager), verbatim but for the names it
    reads from lib/out."""

    def __init__(self, title=None, *, width_=None, stream=None):
        self.stream = stream
        self.w = out.width(stream) if width_ is None else width_
        self._lines = []
        if title:
            self._lines.extend(out.wrap(title, self.w))

    def blank(self):
        self._lines.append("")
        return self

    def line(self, text=""):
        text = out.printable(text) if self.w > 0 else str(text)
        self._lines.extend(text.split("\n"))
        return self

    def section(self, heading):
        self.blank()
        self._lines.append(out.printable(heading) if self.w > 0 else heading)
        return self

    def para(self, text, indent=""):
        self._lines.extend(out.wrap(text, self.w, indent, indent))
        return self

    def item(self, text, indent="", bullet="- "):
        self._lines.extend(out.wrap(text, self.w, indent + bullet,
                                    indent + " " * len(bullet)))
        return self

    def items(self, texts, indent=""):
        for t in texts:
            self.item(t, indent)
        return self

    def kv(self, pairs, indent=""):
        self._lines.extend(out.kv_lines(pairs, indent, self.w))
        return self

    def table(self, headers, body, **kw):
        kw.setdefault("width_", self.w)
        self._lines.extend(out.fit_table(headers, body, **kw))
        return self

    def message(self, kind, text, details=(), prog=None):
        lines = out.message(kind, text, prog=prog, details=details,
                            width_=self.w)
        self._lines.extend(lines)
        if self.w > 0 and out._entry_len(lines) > 1:
            self._lines.append("")
        return self

    def lines(self):
        res = []
        for ln in self._lines:
            ln = ln.rstrip()
            if not ln and (not res or not res[-1]):
                continue
            res.append(ln)
        while res and not res[-1]:
            res.pop()
        return res

    def text(self):
        return "\n".join(self.lines())

    def print(self, file=None):
        file = sys.stdout if file is None else file
        if self.w > 0:
            out.settle(file)
        for ln in self.lines():
            print(ln, file=file)


LONG = ("A sentence long enough to wrap at forty columns, with a `tjs sanity --details`"
        " code span that never breaks and a ticker AB\x1bC.TO carrying an escape.")
HEAD = ["ACCOUNT", "SYMBOL", "QTY", "COST"]
ROWS = [["margin", "QZQ.TO", "100", "240.10"], ["tfsa", "SAMPA.TO", "-5", "34.00"]]


def build(doc_class):
    """One script of Doc calls, the same for the old Doc and the new."""
    d = doc_class("TITLE — a made-up report of positions held at the end of the year, long")
    d.blank().blank().line("  verbatim  \x07 row").section("SECTION")
    d.para(LONG, "  ").item(LONG).items(["one", LONG], "  ")
    d.kv([("account", "margin"), ("a longer label", LONG), ("path", out.Verbatim("a/b " * 15))])
    d.table(HEAD, ROWS, drop=(3,))
    d.message("warning", LONG, details=["  - a detail line"])
    d.message("note", "one line")
    d.blank().blank()
    return d


class TestTheDocIsTheSameAsBefore(unittest.TestCase):
    """The 37 builders' Docs: the same calls, the same lines and bytes."""

    def test_the_same_lines_and_bytes(self):
        for w in WIDTHS:
            for settling in (False, True):
                with self.subTest(width=w, settling=settling):
                    def old():
                        build(OldDoc).print()

                    def new():
                        build(out.Doc).print()
                    self.assertEqual(captured(new, w, settling), captured(old, w, settling))
            with self.subTest(width=w, captured=True):
                self.assertEqual(owing(lambda: build(out.Doc).print(), w),
                                 owing(lambda: build(OldDoc).print(), w))
            with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(w)}):
                self.assertEqual(build(out.Doc).lines(), build(OldDoc).lines())
                self.assertEqual(build(out.Doc).text(), build(OldDoc).text())

    def test_an_empty_doc_settles_as_before(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                self.assertEqual(captured(lambda: out.Doc().print(), w, True),
                                 captured(lambda: OldDoc().print(), w, True))

    def test_a_list_reused_after_a_call_changes_nothing_added(self):
        rows = [list(r) for r in ROWS]
        d = out.Doc(width_=120).table(HEAD, rows)
        texts = ["first"]
        d.items(iter(texts))
        was = d.lines()
        rows.append(["rrsp", "NEW.TO", "1", "1.00"])
        rows[0][1] = "CHANGED"
        texts.append("second")
        self.assertEqual(d.lines(), was)


class TestExact(unittest.TestCase):
    """An exact Doc prints what the raw print() calls it replaces printed."""

    @staticmethod
    def old():
        print("TITLE — kept as is, never wrapped even when it is longer than forty columns  ")
        print()
        print()
        print("a", "b", 3, sep="|")
        print("no newline", end="")
        print(" then flushed", flush=True)
        print("x\x1b[31my  ")
        print("two\nlines")
        print("")
        for ln in out.wrap(LONG, None, "  ", "    "):
            print(ln)
        for ln in out.fit_table(HEAD, ROWS):
            print(ln)
        print(out.act("1 symbol spelling", "tjs list"))
        print(out.details_hint("tjs sanity --details"))
        print("", end="")
        print("ends", None, sep=None, end=None)

    @staticmethod
    def new():
        d = out.Doc("TITLE — kept as is, never wrapped even when it is longer than forty columns  ",
                    exact=True)
        d.blank().blank()
        d.raw("a", "b", 3, sep="|")
        d.raw("no newline", end="")
        d.raw(" then flushed", flush=True)
        d.line("x\x1b[31my  ")
        d.line("two\nlines")
        d.line("")
        d.para(LONG, "  ", hang="    ")
        d.table(HEAD, ROWS)
        d.act("1 symbol spelling", "tjs list")
        d.hint("tjs sanity --details")
        d.raw("", end="")
        d.raw("ends", None, sep=None, end=None)
        return d

    def test_the_bytes_and_the_flushes(self):
        for w in WIDTHS:
            for settling in (False, True):
                with self.subTest(width=w, settling=settling):
                    self.assertEqual(captured(lambda: out.render(self.new()), w, settling),
                                     captured(self.old, w, settling))

    def test_a_captured_stream_keeps_the_blank_line_owed(self):
        """A raw print never settled: on a captured stream the blank line a
        message owes is still owed after an exact Doc, as after the prints."""
        for w in WIDTHS:
            with self.subTest(width=w):
                want = owing(self.old, w)
                self.assertEqual(want[1], w > 0)
                self.assertEqual(owing(lambda: out.render(self.new()), w), want)
                self.assertEqual(owing(lambda: self.new().print(), w), want)

    def test_an_empty_title_is_a_blank_line(self):
        """print("") printed one; Doc("", exact=True) prints it too (no
        title prints nothing; a Doc that is not exact drops an empty one,
        as before)."""
        for w in WIDTHS:
            with self.subTest(width=w):
                self.assertEqual(captured(lambda: out.Doc("", exact=True).raw("x").print(), w),
                                 captured(lambda: (print(""), print("x")), w))
                self.assertEqual(captured(lambda: out.Doc(None, exact=True).raw("x").print(), w), "x\n")
        self.assertEqual(out.Doc("", exact=True).lines(), [""])
        self.assertEqual(out.Doc("", width_=120).lines(), OldDoc("", width_=120).lines())

    def test_the_lines_are_those_printed(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                printed = captured(self.old, w).replace(FLUSH, "")
                with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(w)}):
                    self.assertEqual(self.new().lines(), printed[:-1].split("\n"))

    def test_a_section_and_a_message_add_nothing(self):
        d = out.Doc(width_=120, exact=True)
        d.section("HEADING  \x1b").message("warning", "first line", details=["second"])
        shown = out.message("warning", "first line", details=["second"], width_=120)
        self.assertGreater(len(shown), 1)
        self.assertEqual(d.lines(), ["", "HEADING  \x1b", *shown])

    def test_raw_is_for_an_exact_doc_only(self):
        with self.assertRaises(ValueError):
            out.Doc(width_=0).raw("x")


class TestTheLongForm(unittest.TestCase):
    def doc(self, exact=False):
        d = out.Doc("TITLE", width_=120, exact=exact)
        d.legend("QTY: shares; COST: the books' cost.").table(HEAD, ROWS)
        with d.details():
            d.blank().line("detail one").blank()
            d.blank()
        d.blank().act("1 short in the books", "tjs find-missing-history")
        d.hint("tjs sanity --details")
        return d

    def test_shown_only_with_details(self):
        d = self.doc()
        short, long_ = d.lines(), d.lines(details=True)
        self.assertNotIn("detail one", short)
        self.assertIn("detail one", long_)
        self.assertEqual(short[-3:], ["", "! 1 short in the books — tjs find-missing-history",
                                      "More: tjs sanity --details (notes)"])
        for lines in (short, long_):             # folded either way
            self.assertNotIn(("", ""), list(zip(lines, lines[1:])))
        self.assertEqual(captured(lambda: out.render(self.doc(), details=True), 0),
                         "\n".join(long_) + "\n")
        self.assertEqual(captured(lambda: out.render(self.doc()), 0), "\n".join(short) + "\n")

    def test_exact_keeps_the_blank_lines_of_either_form(self):
        d = self.doc(exact=True)
        self.assertEqual(d.lines(details=True).count(""), d.lines().count("") + 3)

    def test_an_exact_doc_prints_either_form_as_the_prints_did(self):
        def old(details):
            def prints():
                print("TITLE")
                print("QTY: shares; COST: the books' cost.")
                for ln in out.fit_table(HEAD, ROWS, width_=120):
                    print(ln)
                if details:
                    print("")
                    print("detail one")
                    print("")
                    print("")
                print("")
                print(out.act("1 short in the books", "tjs find-missing-history"))
                print(out.details_hint("tjs sanity --details"))
            return prints
        for details in (False, True):
            for w in WIDTHS:
                with self.subTest(details=details, width=w):
                    want = captured(old(details), w)
                    self.assertEqual(captured(lambda: out.render(self.doc(exact=True), details=details), w),
                                     want)
                    self.assertEqual(captured(lambda: self.doc(exact=True).print(details=details), w), want)
                    self.assertEqual(self.doc(exact=True).lines(details), want[:-1].split("\n"))

    def test_the_named_parts(self):
        d = self.doc()
        self.assertEqual([k for k, _c, _l in d._ops if k in ("legend", "table", "act", "hint")],
                         ["legend", "table", "act", "hint"])


class TestJson(unittest.TestCase):
    PAYLOADS = (
        {"b": float("nan"), "a": [1.5, float("inf"), (float("-inf"), {"z": None, "y": 2})]},
        {"when": datetime.date(2024, 11, 25), "path": PurePosixPath("reports/x.rpt"),
         "amount": Decimal("12.30"), "name": "Zürich — made up", "nested": {"k": [[]], "e": {}}},
        [],
        {"n": 1e300 * 10},
    )

    def test_the_bytes_of_the_old_json_out(self):
        for p in self.PAYLOADS:
            for w in WIDTHS:
                for settling in (False, True):
                    with self.subTest(payload=p, width=w, settling=settling):
                        want = captured(lambda: old_json_out(p), w, settling)
                        self.assertEqual(captured(lambda: sanity._json_out(p), w, settling), want)

                        def rendered():
                            d = out.Doc()
                            d.json = p
                            out.render(d, as_json=True)
                        self.assertEqual(captured(rendered, w, settling), want)

    def test_a_captured_stream_keeps_the_blank_line_owed(self):
        """The old _json_out was a raw print: it never settled."""
        for p in self.PAYLOADS:
            for w in WIDTHS:
                with self.subTest(payload=p, width=w):
                    want = owing(lambda: old_json_out(p), w)
                    self.assertEqual(want[1], w > 0)
                    self.assertEqual(owing(lambda: sanity._json_out(p), w), want)

                    def rendered():
                        d = out.Doc()
                        d.json = p
                        out.render(d, as_json=True)
                    self.assertEqual(owing(rendered, w), want)

    def test_a_site_keeps_its_own_keywords(self):
        p = self.PAYLOADS[0]
        d = out.Doc()
        d.json, d.json_spec = p, {"indent": 2, "clean": False}
        shown = captured(lambda: out.render(d, as_json=True), 0)
        self.assertEqual(shown, json.dumps(p, indent=2) + "\n")
        self.assertIn('"b": NaN', shown)                 # not cleaned: NaN stays NaN
        self.assertIn("Infinity", shown)
        self.assertEqual(out.clean_json(p), json.loads(captured(lambda: old_json_out(p), 0)))

    def test_a_spec_without_clean_is_refused(self):
        """No default: True would turn a site's NaN into null, False a
        _json_out site's null into NaN; and `clean` is True or False, not
        a value that reads as one."""
        for spec in ({"indent": 2, "sort_keys": True}, {}, {"clean": None}, {"clean": 1},
                     {"clean": 0}, {"clean": "yes"}, {"indent": 2, "clean": "False"}):
            with self.subTest(spec=spec):
                d = out.Doc(width_=0)
                d.json, d.json_spec = {"n": float("nan")}, spec
                buf = io.StringIO()
                with self.assertRaisesRegex(ValueError, "clean"):
                    out.render(d, as_json=True, file=buf)
                self.assertEqual(buf.getvalue(), "")
        self.assertIs(out.JSON_SPEC["clean"], True)


class TestTheReportTable(unittest.TestCase):
    ROWS = ["ACCOUNTS SYMBOL ISSUE TAXJSON HOLDINGS DIFF",
            "margin SAMPA.TO QTY_MISMATCH 100 90 +10",
            "margin+tfsa QZQ.US MISSING_IN_HOLDINGS 150 0 +150",
            "TOTAL - - 250 90 +160"]

    def test_the_bytes_of_the_old_table(self):
        for kw in ({}, {"rule_before_last": True}, {"padding": "  "},
                   {"fit": True, "drop": (4,), "key": (0, 1)}, {"fit": True, "rule_before_last": True},
                   {"fit": True, "width_": 40}):
            for w in WIDTHS:
                for settling in (False, True):
                    with self.subTest(kw=kw, width=w, settling=settling):
                        self.assertEqual(
                            captured(lambda: sanity._print_report_table(self.ROWS, **kw), w, settling),
                            captured(lambda: old_print_report_table(self.ROWS, **kw), w, settling))
                with self.subTest(kw=kw, width=w, captured=True):
                    want = owing(lambda: old_print_report_table(self.ROWS, **kw), w)
                    self.assertEqual(want[1], w > 0)
                    self.assertEqual(owing(lambda: sanity._print_report_table(self.ROWS, **kw), w), want)


class TestLive(unittest.TestCase):
    """A live exact Doc prints each op the moment it is added, as the raw
    print() calls it replaces printed them then (commit 1h)."""

    @staticmethod
    def new():
        d = out.Doc("TITLE — kept as is, never wrapped even when it is longer than forty columns  ",
                    exact=True, live=True)
        d.blank().blank()
        d.raw("a", "b", 3, sep="|")
        d.raw("no newline", end="")
        d.raw(" then flushed", flush=True)
        d.line("x\x1b[31my  ")
        d.line("two\nlines")
        d.line("")
        d.para(LONG, "  ", hang="    ")
        d.table(HEAD, ROWS)
        d.act("1 symbol spelling", "tjs list")
        d.hint("tjs sanity --details")
        d.raw("", end="")
        d.raw("ends", None, sep=None, end=None)
        return d

    def test_the_bytes_and_the_flushes_with_no_render(self):
        for w in WIDTHS:
            for settling in (False, True):
                with self.subTest(width=w, settling=settling):
                    self.assertEqual(captured(self.new, w, settling),
                                     captured(TestExact.old, w, settling))

    def test_no_line_is_kept(self):
        """TJ-4873: a live Doc keeps nothing it printed (a long report
        streams, as `tjs audit`'s raw prints did, TJ-0334); lines() and
        text() refuse rather than answer with nothing."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            d = self.new()
            for i in range(10_000):
                d.line(f"row {i}")
        self.assertEqual(d._ops, [])
        self.assertTrue(buf.getvalue().endswith("row 9998\nrow 9999\n"))
        for read in (d.lines, d.text):
            with self.subTest(read=read.__name__), self.assertRaises(ValueError):
                read()

    def test_each_op_is_printed_when_it_is_added(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            d = out.Doc(width_=0, exact=True, live=True)
            d.line("one")
            self.assertEqual(buf.getvalue(), "one\n")
            d.raw("two", end="")
            self.assertEqual(buf.getvalue(), "one\ntwo")
            d.blank()
            self.assertEqual(buf.getvalue(), "one\ntwo\n")

    def test_stdout_as_it_is_at_each_op(self):
        first, second = io.StringIO(), io.StringIO()
        d = out.Doc(width_=0, exact=True, live=True)
        with contextlib.redirect_stdout(first):
            d.line("a")
        with contextlib.redirect_stdout(second):
            d.line("b")
        self.assertEqual((first.getvalue(), second.getvalue()), ("a\n", "b\n"))

    def test_a_report_that_stops_has_printed_what_print_had(self):
        def report(printing):
            emit = print if printing else out.Doc(width_=0, exact=True, live=True).line
            emit("before")
            raise RuntimeError("a row that will not format")

        for printing in (True, False):
            buf = io.StringIO()
            with self.subTest(printing=printing), contextlib.redirect_stdout(buf):
                with self.assertRaises(RuntimeError):
                    report(printing)
            self.assertEqual(buf.getvalue(), "before\n")

    def test_to_a_stream_of_its_own(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()) as o:
            out.Doc(width_=0, exact=True, live=True, stream=err).line("x")
        self.assertEqual((err.getvalue(), o.getvalue()), ("x\n", ""))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            out.Doc(width_=0, live=True)
        d = out.Doc(width_=0, exact=True, live=True)
        with self.assertRaises(ValueError):
            with d.details():
                pass
        with self.assertRaises(ValueError):
            d.print()
        with self.assertRaises(ValueError):
            out.render(d)


class TestPut(unittest.TestCase):
    def test_lines_as_print_prints_them_owing_nothing(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                def new():
                    out.put(["one  ", "two\x1b"])
                    out.put("three")
                    out.put([], sys.stdout)
                    print("next")

                def old():
                    print("one  ")
                    print("two\x1b")
                    print("three")
                    print("next")
                self.assertEqual(captured(new, w, True), captured(old, w, True))
                self.assertEqual(owing(new, w), owing(old, w))

    def test_to_another_stream(self):
        err = io.StringIO()
        out.put(["a", "b"], err)
        self.assertEqual(err.getvalue(), "a\nb\n")


if __name__ == "__main__":
    unittest.main()

"""A ticker.map file is parsed once a process while what the parse reads
is unchanged (backlog TJ-4961): `tjs run` read the demo book's map 103
times, each a full parse of its 1,900 lines with the tobase.map overlay.
lib/map_file `_parse_map_file` keeps each parse (`_PARSED`), keyed on the
file's text as read and its overlay, never its modification time; every
caller gets a copy of its own; the problems and notes come back on every
call, in the same order; a file that is gone or unreadable raises as
before.

Every map is synthetic (ZZ*, QZ* tickers). A test map carries a comment
naming the test, so no other test's parse in this process is a hit.
"""
import copy
import dataclasses
import io
import itertools
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from taxjson.lib import map_file
from taxjson.lib.map_file import _parse_map_file, load_map_file, map_file_problems
from taxjson.lib.ticker_map_rules import merge_renames
from _style import CapturedWidth
from _support.symbols_interlisted_master import F1, F2, _project


# The loader's lines are compared in full (TAXJSON_WIDTH=0), run alone too.
_WIDTH = CapturedWidth()
_SEQ = itertools.count()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _text(test: unittest.TestCase, body: str) -> str:
    """`body` under a comment no other test's map carries."""
    return f"# {test.id()} {next(_SEQ)}\n{body}"


def _counted():
    """A patch counting the map parses _parse_map_file makes."""
    return mock.patch.object(map_file, "_parse_map_text",
                             wraps=map_file._parse_map_text)


_RICH = ("GLOBAL ZZOL.US ZZNW.US\n"
         "TOBASE ZZNW.US ZZNW.TO\n"
         "DELETE ZZJK.TO\n"
         "DISTINCT ZZCD.TO ZZCD.US\n"
         "RENAME ZZRA.US ZZRB.US 2024-03-01\n"
         "GLOBAL ZZCa.US ZZCA.US\n"
         "QUOTE ZZQT.TO ZZQT.US\n"
         "DISTINCT ZZSM.US ZZSM.US\n"
         "BOGUS ZZBG.US\n")


def _immutable(x) -> bool:
    if isinstance(x, (str, int, float, bool, type(None))):
        return True
    if isinstance(x, (tuple, frozenset)):
        return all(_immutable(i) for i in x)
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return (type(x).__dataclass_params__.frozen
                and all(_immutable(getattr(x, f.name)) for f in dataclasses.fields(x)))
    return False


class TestParsedOnce(unittest.TestCase):

    def test_the_same_map_is_parsed_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(_text(self, "GLOBAL ZZOL.US ZZNW.US\n"), encoding="utf-8")
            with _counted() as parse, redirect_stderr(io.StringIO()):
                first = _parse_map_file(p)
                again = [_parse_map_file(p), _parse_map_file(str(p)),
                         (load_map_file(p), [], []), (load_map_file(p), [], []),
                         (_parse_map_file(p)[0], map_file_problems(p), [])]
            self.assertEqual(parse.call_count, 1)
            for got in again:
                self.assertEqual(got, first)
            self.assertEqual(merge_renames(first[0], False), {"ZZOL.US": "ZZNW.US"})

    def test_a_project_map_and_its_overlay_are_parsed_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker=_text(self, "GLOBAL ZZOL.US ZZNW.US\n"),
                            tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            with _counted() as parse:
                first = _parse_map_file(root / "ticker.map")
                # the map with its overlay, then the map's own lines
                self.assertEqual(parse.call_count, 2)
                second = _parse_map_file(root / "ticker.map")
                self.assertEqual(parse.call_count, 2)
            self.assertEqual(second, first)
            self.assertEqual(merge_renames(first[0], True)["QZAB.US"], "QZA.TO")
            self.assertIn("QZAB.US", first[0].generated)

    def test_the_cache_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            for i in range(map_file._PARSED_MAX + 3):
                p = Path(td) / f"m{i}" / "ticker.map"
                p.parent.mkdir()
                p.write_text(_text(self, f"DELETE ZZD{i}.US\n"), encoding="utf-8")
                self.assertEqual(_parse_map_file(p)[0].delete, {f"ZZD{i}.US"})
        self.assertLessEqual(len(map_file._PARSED), map_file._PARSED_MAX)


class TestWhatTheParseReads(unittest.TestCase):
    """The key is what the parse reads; a file is read on every call."""

    def test_a_rewrite_in_the_same_tick_at_the_same_size_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            head = _text(self, "")
            p.write_text(head + "GLOBAL ZZOL.US ZZAA.US\n", encoding="utf-8")
            st = p.stat()
            self.assertEqual(merge_renames(_parse_map_file(p)[0], False),
                             {"ZZOL.US": "ZZAA.US"})
            p.write_text(head + "GLOBAL ZZOL.US ZZBB.US\n", encoding="utf-8")
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertEqual((p.stat().st_size, p.stat().st_mtime_ns),
                             (st.st_size, st.st_mtime_ns))
            self.assertEqual(merge_renames(_parse_map_file(p)[0], False),
                             {"ZZOL.US": "ZZBB.US"})

    def test_a_changed_tobase_map_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker=_text(self, ""),
                            tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            self.assertNotIn("QZCD.US", _parse_map_file(root / "ticker.map")[0].tobase)
            (root / "tobase.map").write_text(
                f"TOBASE QZAB.US QZA.TO  # master:{F1}\n"
                f"TOBASE QZCD.US QZC.TO  # master:{F2}\n")
            tm = _parse_map_file(root / "ticker.map")[0]
            self.assertEqual(tm.tobase.get("QZCD.US"), "QZC.TO")
            self.assertIn("QZCD.US", tm.generated)

    def test_the_same_text_in_another_file_names_that_file(self):
        with tempfile.TemporaryDirectory() as td:
            text = _text(self, "BOGUS ZZBG.US\n")
            a, b = Path(td) / "ticker.map", Path(td) / "other.map"
            a.write_text(text, encoding="utf-8")
            b.write_text(text, encoding="utf-8")
            self.assertTrue(map_file_problems(a)[0].startswith("ticker.map:2: "))
            self.assertTrue(map_file_problems(b)[0].startswith("other.map:2: "))

    def test_a_map_deleted_after_a_read_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(_text(self, "DELETE ZZJK.TO\n"), encoding="utf-8")
            self.assertEqual(_parse_map_file(p)[0].delete, {"ZZJK.TO"})
            p.unlink()
            with self.assertRaises(FileNotFoundError):
                _parse_map_file(p)
            with self.assertRaises(FileNotFoundError):
                load_map_file(p)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads a file of mode 000")
    def test_a_map_made_unreadable_after_a_read_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(_text(self, "DELETE ZZJK.TO\n"), encoding="utf-8")
            self.assertEqual(_parse_map_file(p)[0].delete, {"ZZJK.TO"})
            p.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    _parse_map_file(p)
            finally:
                p.chmod(0o600)


class TestEachCallerOwnsItsCopy(unittest.TestCase):

    def _map(self, td) -> Path:
        p = Path(td) / "ticker.map"
        p.write_text(_text(self, _RICH), encoding="utf-8")
        return p

    def test_a_caller_that_changes_its_map_changes_no_other_callers(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._map(td)
            tm, problems, notes = _parse_map_file(p)
            pristine = copy.deepcopy((tm, problems, notes))
            self.assertTrue(problems and notes and tm.case_spellings)
            for field, value in tm._asdict().items():
                if isinstance(value, dict):
                    value["ZZX.US"] = "ZZY.US"
                elif isinstance(value, set):
                    value.add("ZZX.US")
            problems.append("ticker.map:99: planted")
            notes.clear()
            self.assertEqual(_parse_map_file(p), pristine)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(load_map_file(p), pristine[0])

    def test_every_field_is_copied_or_immutable(self):
        # A field of another mutable kind, or a dict, set or list holding
        # one, would be shared between callers: _own_copy copies the
        # dicts, sets and lists of a parse, one level.
        with tempfile.TemporaryDirectory() as td:
            p = self._map(td)
            (one, p1, n1), (two, p2, n2) = _parse_map_file(p), _parse_map_file(p)
            self.assertTrue(one.dated and one.lookup_named and one.case_spellings)
            self.assertIsNot(p1, p2)
            self.assertIsNot(n1, n2)
            for field, a in one._asdict().items():
                b = getattr(two, field)
                self.assertEqual(a, b, field)
                if isinstance(a, (dict, set, list)):
                    self.assertIsNot(a, b, field)
                    items = list(a.items()) if isinstance(a, dict) else list(a)
                elif isinstance(a, MappingProxyType):
                    items = list(a.items())
                else:
                    items = [a]
                for item in items:
                    self.assertTrue(_immutable(item), f"{field}: {item!r}")


class TestMessagesEveryCall(unittest.TestCase):
    """load_map_file says a map's problems and notes on every call while
    the output is captured, as when each call parsed the file."""

    def test_problems_and_notes_on_every_call_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(_text(self, "BOGUS ZZBG.US\nDISTINCT ZZSM.US ZZSM.US\n"),
                         encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                load_map_file(p)
                once = err.getvalue()
                load_map_file(p)
            lines = once.splitlines()
            self.assertEqual(len(lines), 2, once)
            self.assertTrue(lines[0].startswith(
                "warning: ticker.map problem: ticker.map:2: line has no "
                "ticker.map keyword"), lines[0])
            self.assertTrue(lines[0].endswith("(`tjs run` refuses this map)"), lines[0])
            self.assertEqual(lines[1], "warning: ticker.map:3: DISTINCT pairs a "
                                       "symbol with itself (no effect): "
                                       "'DISTINCT ZZSM.US ZZSM.US' — skipping")
            self.assertEqual(err.getvalue(), once * 2)
            self.assertEqual(map_file_problems(p), map_file_problems(p))
            self.assertEqual(len(map_file_problems(p)), 1)


if __name__ == "__main__":
    unittest.main()


class TestALinkUnderAnotherNameNamesItself(unittest.TestCase):
    """The file's name is in the key: a problem line names the file it was
    read as, so a link to the same map under another name is another
    parse (users link one ticker.map into several year folders)."""

    def test_each_name_is_in_its_own_problem_lines(self):
        with tempfile.TemporaryDirectory(prefix="mapname-") as tmp:
            real = Path(tmp) / "ticker.map"
            real.write_text(_text(self, "NOSUCHKEYWORD ZZAA.US ZZBB.US\n"),
                            encoding="utf-8")
            link = Path(tmp) / "other.map"
            os.symlink(real, link)
            first = map_file_problems(real)
            second = map_file_problems(link)
        self.assertTrue(first and second)
        self.assertTrue(all(str(p).startswith("ticker.map:") for p in first), first)
        self.assertTrue(all(str(p).startswith("other.map:") for p in second), second)

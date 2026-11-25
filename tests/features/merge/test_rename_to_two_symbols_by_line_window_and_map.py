"""Backlog TJ-5283 (after TJ-4985): a rename of one holding to two
symbols is refused naming each row's file and, for a .tt line, its line;
copies of the rename within the split-date window are compared, not only
rows of one day; and the rows are judged as ticker.map maps them.

The refusal named a destination's file ("QZA.TO (in a.tt)"), never the
line the converter's metadata holds (`source_lines`); two rename rows of
one holding two days apart (one event inside SPLIT_DATE_WINDOW_DAYS) with
different destinations were not compared, both booked and the first
applied (`tjs run` exit 0, the holding all QZA.TO, nothing said); and the
check ran in the dedup on the raw symbols: two destinations the map makes
one symbol were refused, and two holdings the map makes one (a rename of
each to another symbol) were booked, the first applied.

Each file is named as the project holds it (`inputs/m/a.tt:2`, the
audit's and the missing-history writer's form), and two rows written
under symbols the map joined can both be true (each listing renamed by its
issuer): the refusal names the ticker.map line that makes the new pair one
first, then deleting the wrong row, never "only one of them can be right".

Synthetic .tt projects (made-up tickers QZO.TO, QZA.TO ...)."""
import io
import tempfile
import tomllib
import unittest
from pathlib import Path

from _support.tt_fx_project import _cli, _tt_project
from tax_rules import rule
from taxjson.bin.taxjson_sort import (RenameDestinationConflict, deduplicate,
                                      rename_conflicts, source_lines_of)
from taxjson.lib.corporate_timeline import SPLIT_DATE_WINDOW_DAYS
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R

BUY = "BUYSELL 2026-01-05 10:00:00 QZO.TO 100 CAD 10 -1000 0"
TO_A = "SPLIT 2026-03-02 09:30:00 QZO.TO QZA.TO 1"
TO_B_LATER = "SPLIT 2026-03-04 09:30:00 QZO.TO QZB.TO 1"
REFUSED = "A rename moves the whole holding to one symbol"


def _rename(day, new, source="", sym="QZO.TO", **kw):
    return TaxTransaction(action="SPLIT", date=day, symbol=sym, quantity=1.0,
                          currency="CAD", account="m", description="NAME CHANGE",
                          symbol_new=new, source=source, **kw)


def _meta(path, lines):
    """A converted .tt file's metadata (taxjson-convert-tt)."""
    return {"source_file": path, "converted_by": "taxjson_convert_tt.py",
            "source_lines": lines}


class TheRenameRowsAreComparedWithinTheWindow(unittest.TestCase):

    def test_two_days_apart_is_one_event_refused(self):
        rows = [_rename("2026-03-02", "QZA.TO", "one.csv"),
                _rename("2026-03-04", "QZB.TO", "two.csv")]
        out = rename_conflicts(rows)
        self.assertEqual(len(out), 1, out)
        for part in ("QZO.TO is renamed on 2026-03-02 and 2026-03-04 in account m "
                     "to 2 different symbols",
                     f"rows within {SPLIT_DATE_WINDOW_DAYS} days of each other are "
                     f"one event", "QZA.TO (on 2026-03-02 in inputs/m/one.csv)",
                     "QZB.TO (on 2026-03-04 in inputs/m/two.csv)", "refused"):
            self.assertIn(part, out[0])
        with self.assertRaises(RenameDestinationConflict):
            deduplicate(list(rows), report=io.StringIO())

    def test_outside_the_window_is_another_event(self):
        # Control: a week and a day apart, two renames (the first moved
        # the holding; the second is its own event).
        rows = [_rename("2026-03-02", "QZA.TO"), _rename("2026-03-10", "QZB.TO")]
        self.assertEqual(rename_conflicts(rows), [])

    def test_a_chain_of_copies_is_one_run(self):
        rows = [_rename("2026-03-02", "QZA.TO"), _rename("2026-03-08", "QZA.TO"),
                _rename("2026-03-14", "QZB.TO")]
        out = rename_conflicts(rows)
        self.assertEqual(len(out), 1, out)
        self.assertIn("2026-03-02, 2026-03-08 and 2026-03-14", out[0])

    def test_copies_that_agree_across_days_are_no_conflict(self):
        rows = [_rename("2026-03-02", "QZA.TO"), _rename("2026-03-04", "QZA.TO")]
        self.assertEqual(rename_conflicts(rows), [])

    def test_one_day_keeps_its_wording(self):
        rows = [_rename("2026-03-02", "QZA.TO", "one.csv"),
                _rename("2026-03-02", "QZB.TO", "two.csv")]
        out = rename_conflicts(rows)
        self.assertIn("QZO.TO is renamed on 2026-03-02 in account m to 2 different "
                      "symbols: QZA.TO (in inputs/m/one.csv) and QZB.TO (in inputs/m/two.csv).",
                      out[0])
        self.assertNotIn("one event", out[0])


class EachRowIsNamedByItsLine(unittest.TestCase):

    def test_a_tt_row_is_named_by_file_and_line(self):
        a = _rename("2026-03-02", "QZA.TO", "a.tt", id="1111aaaa")
        b = _rename("2026-03-02", "QZB.TO", "b.tt", id="2222bbbb")
        lines = source_lines_of([_meta("/p/inputs/m/a.tt", {"1111aaaa": 2}),
                                 _meta("/p/inputs/m/b.tt", {"2222bbbb": 7})])
        out = rename_conflicts([a, b], lines=lines)
        self.assertIn("QZA.TO (in inputs/m/a.tt:2) and QZB.TO (in inputs/m/b.tt:7)", out[0])

    def test_a_relabelled_copy_reads_its_own_files_line(self):
        # The dedup keeps a line of a second .tt file as its own record
        # (`<id>~2`): its own file's line, not the first file's.
        a = _rename("2026-03-02", "QZA.TO", "a.tt", id="1111aaaa")
        b = _rename("2026-03-02", "QZB.TO", "b.tt", id="2222bbbb~2")
        lines = source_lines_of([_meta("/p/inputs/m/a.tt", {"1111aaaa": 2,
                                                            "2222bbbb": 9}),
                                 _meta("/p/inputs/m/b.tt", {"2222bbbb": 4})])
        out = rename_conflicts([a, b], lines=lines)
        self.assertIn("QZB.TO (in inputs/m/b.tt:4)", out[0])

    def test_an_export_row_and_a_corporate_action_are_named_as_before(self):
        a = _rename("2026-03-02", "QZA.TO", "ib.csv", id="1111aaaa")
        b = _rename("2026-03-02", "QZB.TO", corp_event_id="ev-r")
        out = rename_conflicts([a, b], lines=source_lines_of(
            [{"source_file": "/p/ib.csv"}, "not a block"]))
        self.assertIn("QZA.TO (in inputs/m/ib.csv) and QZB.TO (corporate action ev-r)",
                      out[0])

    def test_the_spelling_before_the_map_is_named(self):
        a = _rename("2026-03-02", "QZA.TO", "a.tt")
        b = _rename("2026-03-02", "QZB.TO", "b.tt")
        written = {id(b): (b, "QZOX.TO", "QZB.TO")}
        out = rename_conflicts([a, b], written=written)
        self.assertIn("QZB.TO (in inputs/m/b.tt, written QZOX.TO -> QZB.TO)", out[0])
        self.assertIn("Symbols are shown as ticker.map maps them.", out[0])
        # Written under two symbols the map joined: both can be right
        # (each listing renamed by its issuer), so the map line comes first.
        self.assertIn("add `GLOBAL QZB.TO QZA.TO` to ticker.map", out[0])
        self.assertNotIn("only one of them can be right", out[0])

    def test_the_merge_leaves_the_refusal_to_after_its_map(self):
        rows = [_rename("2026-03-02", "QZA.TO", "a.tt"),
                _rename("2026-03-02", "QZB.TO", "b.tt")]
        self.assertEqual(len(deduplicate(list(rows), check_renames=False)), 2)


def _project(td, *, b_lines, ticker_map="", a_lines=(BUY, TO_A), country="canada"):
    root, home = _tt_project(td, list(a_lines), country=country)
    (root / "inputs/m/b.tt").write_text("# kept by hand\n" + "\n".join(b_lines) + "\n")
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    return root, home, _cli(root, home, "run", "--no-input")


def _held(root: Path) -> dict:
    doc = tomllib.loads((root / "reports/m_holdings.toml").read_text(encoding="utf-8"))
    out = {}

    def walk(x):
        if isinstance(x, dict):
            if "symbol" in x and "quantity" in x:
                out[x["symbol"]] = float(x["quantity"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc)
    return out


class TheRunRefusesARenameToTwoSymbols(unittest.TestCase):

    @rule(R.CA_ACB_RENAME)
    def test_the_entry_two_tt_files_two_days_apart(self):
        # The entry's repro: exit 0 on base, the holding all QZA.TO.
        with tempfile.TemporaryDirectory() as td:
            root, _home, r = _project(td, b_lines=[TO_B_LATER])
            self.assertEqual(r.returncode, 1, r.stdout[-1500:] + r.stderr[-1500:])
            for part in ("QZO.TO is renamed on 2026-03-02 and 2026-03-04 in account m "
                         "to 2 different symbols", "QZA.TO (on 2026-03-02 in inputs/m/a.tt:2)",
                         "QZB.TO (on 2026-03-04 in inputs/m/b.tt:2)", REFUSED):
                self.assertIn(part, r.stderr)
            self.assertFalse((root / "work/m_gains.json").exists())

    @rule(R.US_BASIS_RENAME)
    def test_the_entry_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            lines = (BUY.replace(".TO", ".US").replace(" CAD ", " USD "),
                     TO_A.replace(".TO", ".US"))
            root, _home, r = _project(td, a_lines=lines, country="usa",
                                      b_lines=[TO_B_LATER.replace(".TO", ".US")])
            self.assertEqual(r.returncode, 1, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertIn("QZA.US (on 2026-03-02 in inputs/m/a.tt:2) and QZB.US (on "
                          "2026-03-04 in inputs/m/b.tt:2)", r.stderr)

    @rule(R.CA_ACB_RENAME)
    def test_two_destinations_the_map_makes_one_are_one_rename(self):
        # Base refused this ("to 2 different symbols: QZA.TO (in a.tt)
        # and QZAX.TO (in b.tt)"): after the map it is one rename,
        # applied once, as the engines read it.
        with tempfile.TemporaryDirectory() as td:
            root, _home, r = _project(
                td, b_lines=["SPLIT 2026-03-02 09:30:00 QZO.TO QZAX.TO 1"],
                ticker_map="GLOBAL QZAX.TO QZA.TO\n")
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertNotIn(REFUSED, r.stdout + r.stderr)
            self.assertEqual(_held(root), {"QZA.TO": 100.0})
            self.assertIn("duplicate split: QZO.TO -> QZA.TO x1 on 2026-03-02 appears "
                          "2 times in account m", (root / "reports/m.sum").read_text(
                              encoding="utf-8"))

    @rule(R.CA_ACB_RENAME)
    def test_two_holdings_the_map_makes_one_renamed_apart_are_refused(self):
        # Base booked it (exit 0): QZOX.TO's 10 units joined QZO.TO, the
        # rename to QZA.TO applied to all 110 and the one to QZB.TO found
        # nothing.
        with tempfile.TemporaryDirectory() as td:
            root, _home, r = _project(
                td, b_lines=["BUYSELL 2026-01-06 10:00:00 QZOX.TO 10 CAD 10 -100 0",
                             "SPLIT 2026-03-02 09:30:00 QZOX.TO QZB.TO 1"],
                ticker_map="GLOBAL QZOX.TO QZO.TO\n")
            self.assertEqual(r.returncode, 1, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertIn("QZO.TO is renamed on 2026-03-02 in account m to 2 different "
                          "symbols: QZA.TO (in inputs/m/a.tt:2) and QZB.TO (in "
                          "inputs/m/b.tt:3, written QZOX.TO -> QZB.TO)", r.stderr)
            self.assertIn("Symbols are shown as ticker.map maps them.", r.stderr)
            self.assertIn("add `GLOBAL QZB.TO QZA.TO` to ticker.map", r.stderr)

    @rule(R.CA_ACB_RENAME)
    def test_copies_of_one_rename_two_days_apart_are_booked_once(self):
        # Control (unchanged): the same rename two days apart is one
        # event, applied once on the earlier date.
        with tempfile.TemporaryDirectory() as td:
            root, _home, r = _project(
                td, b_lines=["SPLIT 2026-03-04 09:30:00 QZO.TO QZA.TO 1"])
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertEqual(_held(root), {"QZA.TO": 100.0})
            self.assertIn("QZO.TO -> QZA.TO is booked on 2026-03-02 (x1) and 2026-03-04 "
                          "(x1) in account m", (root / "reports/m.sum").read_text(
                              encoding="utf-8"))


class _TwoListingsTheMapJoinsCases:
    """Two listings ticker.map makes one holding (`GLOBAL QZOX QZO`), both
    bought and each renamed by its issuer (QZO to QZA, QZOX to QZAX): both
    rows can be true, so the refusal names the ticker.map line that makes
    the new pair one first, then deleting the wrong row; with that line the
    run books it (v2.1.0 held QZA 15). Review of TJ-5283."""
    COUNTRY = ""
    SUF = ""
    CUR = ""

    def _lines(self, *ls):
        return [ln.replace(".TO", self.SUF).replace(" CAD ", f" {self.CUR} ")
                for ln in ls]

    def _run(self, td, ticker_map, renames):
        root, home = _tt_project(td, self._lines(
            "BUYSELL 2026-01-05 10:00:00 QZO.TO 10 CAD 10 -100 0",
            "BUYSELL 2026-01-06 10:00:00 QZOX.TO 5 CAD 10 -50 0", *renames),
            country=self.COUNTRY)
        (root / "ticker.map").write_text(ticker_map.replace(".TO", self.SUF))
        return root, _cli(root, home, "run", "--no-input")

    def test_the_refusal_names_the_map_line_first(self):
        s = self.SUF
        with tempfile.TemporaryDirectory() as td:
            _root, r = self._run(td, "GLOBAL QZOX.TO QZO.TO\n", (
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZA.TO 1",
                "SPLIT 2026-03-02 09:30:00 QZOX.TO QZAX.TO 1"))
            self.assertEqual(r.returncode, 1, r.stdout[-1500:] + r.stderr[-1500:])
            said = " ".join(r.stderr.split())
            self.assertIn(
                f"dedup: QZO{s} is renamed on 2026-03-02 in account m to 2 different "
                f"symbols: QZA{s} (in inputs/m/a.tt:3) and QZAX{s} (in "
                f"inputs/m/a.tt:4, written QZOX{s} -> QZAX{s}). Symbols are shown "
                f"as ticker.map maps them. The rows were written under different "
                f"symbols (QZO{s}, QZOX{s}) that ticker.map makes one holding, so "
                f"both renames can be right (each listing renamed by its issuer): "
                f"then add `GLOBAL QZAX{s} QZA{s}` to ticker.map, so the new symbols "
                f"are one holding too. Otherwise one row is wrong: delete or "
                f"correct it in the file named", said)
            self.assertNotIn("only one of them can be right", said)

    def test_with_the_map_line_the_run_books_it(self):
        s = self.SUF
        with tempfile.TemporaryDirectory() as td:
            root, r = self._run(td, "GLOBAL QZOX.TO QZO.TO\nGLOBAL QZAX.TO QZA.TO\n", (
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZA.TO 1",
                "SPLIT 2026-03-02 09:30:00 QZOX.TO QZAX.TO 1"))
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertEqual(_held(root), {f"QZA{s}": 15.0})

    def test_control_one_listing_renamed_twice_keeps_delete_or_correct(self):
        # Both rows written under QZO: one of them is false, no map line.
        s = self.SUF
        with tempfile.TemporaryDirectory() as td:
            _root, r = self._run(td, "GLOBAL QZOX.TO QZO.TO\n", (
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZA.TO 1",
                "SPLIT 2026-03-02 09:30:00 QZO.TO QZAX.TO 1"))
            self.assertEqual(r.returncode, 1, r.stdout[-1500:] + r.stderr[-1500:])
            said = " ".join(r.stderr.split())
            self.assertIn(f"QZA{s} (in inputs/m/a.tt:3) and QZAX{s} (in "
                          f"inputs/m/a.tt:4)", said)
            self.assertIn("so only one of them can be right: refused. Keep the right "
                          "one: delete or correct the other row in the file named", said)
            self.assertNotIn("GLOBAL", said)
            self.assertNotIn("both renames can be right", said)


class TestTwoListingsTheMapJoinsCanada(_TwoListingsTheMapJoinsCases, unittest.TestCase):
    COUNTRY, SUF, CUR = "canada", ".TO", "CAD"

    @rule(R.CA_ACB_RENAME)
    def test_the_refusal_names_the_map_line_first(self):
        super().test_the_refusal_names_the_map_line_first()

    @rule(R.CA_ACB_RENAME)
    def test_with_the_map_line_the_run_books_it(self):
        super().test_with_the_map_line_the_run_books_it()

    @rule(R.CA_ACB_RENAME)
    def test_control_one_listing_renamed_twice_keeps_delete_or_correct(self):
        super().test_control_one_listing_renamed_twice_keeps_delete_or_correct()


class TestTwoListingsTheMapJoinsUs(_TwoListingsTheMapJoinsCases, unittest.TestCase):
    COUNTRY, SUF, CUR = "usa", ".US", "USD"

    @rule(R.US_BASIS_RENAME)
    def test_the_refusal_names_the_map_line_first(self):
        super().test_the_refusal_names_the_map_line_first()

    @rule(R.US_BASIS_RENAME)
    def test_with_the_map_line_the_run_books_it(self):
        super().test_with_the_map_line_the_run_books_it()

    @rule(R.US_BASIS_RENAME)
    def test_control_one_listing_renamed_twice_keeps_delete_or_correct(self):
        super().test_control_one_listing_renamed_twice_keeps_delete_or_correct()


if __name__ == "__main__":
    unittest.main()

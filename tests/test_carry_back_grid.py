"""Every reader of a T1A split, against one answer worked out here.

A closed year's split has several readers (the lock, `tjs carryover`, `tjs
handoff`, the estimate) and two nets (the books', a return filed with another
tool). Each bug in TJ-1452, TJ-1473, TJ-1485, TJ-1486 and TJ-1489 was one reader
on another net in one shape. So: the shapes as a grid, and the answer from the
arithmetic of the rule rather than from taxjson's own figures. CA-CARRY-01.

Synthetic data only: made-up tickers, amounts under a thousand.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.years_carryover_ledger import _cli
from _support.ca_t1a_year import BOOKS, _Year

CLAIM = 40.0
FILED = (None, 100.0, -30.0, -100.0, -150.0)


def _split(net, claimed=CLAIM):
    """(carried back, carried forward) of a year's net, nothing carried in."""
    loss = max(0.0, -net)
    back = min(claimed, loss)
    return back, round(loss - back, 2)


class _Grid(unittest.TestCase):
    def check_readers(self, year, books, net):
        """`net`: what the lock's split should rest on."""
        back, forward = _split(net)
        ncl = year.lock()
        self.assertEqual((float(ncl.get("carried_back") or 0.0), ncl["closing"]),
                         (back, forward), "the lock")
        row = year.row()
        self.assertEqual(row["net_gain"], net, "the ledger's net")
        self.assertEqual(row["carried_back"], back, "the ledger's carry-back")
        est = year.estimate()
        books_back, books_forward = _split(books)
        self.assertEqual(est["losses_unused"], max(0.0, -books), "the estimate")
        if est["losses_unused"]:
            self.assertEqual((est["losses_carried_back"],
                              est["losses_carried_forward"]),
                             (books_back, books_forward), "the estimate's split")
        if "filed_gain" in ncl:
            self.assertEqual(est.get("locked_split"), {
                "carried_back": back, "carried_forward": forward},
                "what the estimate says the lock holds")
        # The check every later year runs: it fails on any disagreement.
        self.assertEqual(
            _cli(year.next_year(), "handoff", "--prior",
                 year.y / "filed" / "2023.json").returncode, 0, "handoff")


class TestOneClose(_Grid):
    """Any books, any filed return."""

    @rule(R.CA_CARRY_01)
    def test_every_reader_agrees(self):
        for books in BOOKS:
            for filed in FILED:
                with self.subTest(books=books, filed=filed), \
                        tempfile.TemporaryDirectory() as td:
                    year = _Year(Path(td), books)
                    year.close(filed)
                    self.check_readers(year, books,
                                       books if filed is None else filed)


class TestASecondClose(_Grid):
    """The lock being replaced must not reach the new split (the bug in
    TJ-1473's first fix). A close with no return of its own keeps the filed
    return of the lock it replaces."""

    SEQUENCES = ((-30.0, -50.0), (-100.0, -150.0), (-300.0, -30.0),
                 (100.0, -100.0), (-100.0, 100.0), (-100.0, -100.0),
                 (-30.0, None), (100.0, None), (None, -30.0), (None, None))

    @rule(R.CA_CARRY_01)
    def test_every_reader_agrees(self):
        for books in (100.0, -100.0):
            for first, second in self.SEQUENCES:
                with self.subTest(books=books, first=first, second=second), \
                        tempfile.TemporaryDirectory() as td:
                    year = _Year(Path(td), books)
                    year.close(first)
                    year.close(second)
                    net = next((n for n in (second, first) if n is not None),
                               books)
                    self.check_readers(year, books, net)


class TestABooksMoveAfterTheClose(_Grid):
    """A filed lock's net is the filed return, even one that matched the books
    when it closed, and they moved after (TJ-1485)."""

    @rule(R.CA_CARRY_01)
    def test_the_ledger_keeps_what_was_filed(self):
        with tempfile.TemporaryDirectory() as td:
            year = _Year(Path(td), -100.0)
            year.close(-100.0)
            self.assertNotIn("filed_gain", year.lock())
            frozen = year.y / "inputs" / "margin" / "t.tt"
            self.assertTrue(frozen.exists())
            frozen.write_text(frozen.read_text().replace(
                BOOKS[-100.0], BOOKS[100.0]))
            self.run_again(year)
            row = year.row()
        self.assertEqual((row["net_gain"], row["carried_back"]), (-100.0, 40.0))
        self.assertEqual(row["net_source"], "filed_return")
        self.assertEqual(row["engine_net_gain"], 100.0, "the books did move")

    @staticmethod
    def run_again(year):
        _cli(year.y, "run", "--no-input")


class TestASlipMapAndAFiledLock(_Grid):
    """The closed year's slips are its lock's too (TJ-1494)."""
    MAP = ('\n[[capital_gains_dividends]]\nsymbol = "ZZS.TO"\nyear = 2023\n'
           'amount = "all"\n')

    def _year(self, td):
        year = _Year(Path(td), -100.0)
        (year.top / "inputs" / "margin" / "d.tt").write_text(
            "DIVIDEND 2023-07-15 10:00:00 ZZS.TO 0 CAD 0 60.00 0\n")
        return year

    def _map(self, year):
        toml = year.y / "taxjson.toml"
        toml.write_text(toml.read_text() + self.MAP)
        year.run(year.y, "run", "--no-input")

    @rule(R.CA_CARRY_01)
    def test_a_map_added_after_the_close_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            year = self._year(td)
            year.run(year.y, "run", "--no-input")
            year.close(-50.0)
            before = year.lock()
            self.assertEqual((before["carried_back"], before["closing"]),
                             (40.0, 10.0))
            self._map(year)
            row = year.row()
            self.assertEqual((row["net_gain"], row["carried_back"]),
                             (-50.0, 40.0))
            self.assertEqual(_cli(year.next_year(), "handoff", "--prior",
                                  year.y / "filed" / "2023.json").returncode, 0)

    @rule(R.CA_CARRY_01)
    def test_a_map_there_at_the_close_counts(self):
        """The control: 60.00 of slips over a filed loss of 50.00."""
        with tempfile.TemporaryDirectory() as td:
            year = self._year(td)
            self._map(year)
            year.close(-50.0)
            ncl, row = year.lock(), year.row()
        self.assertEqual((ncl["created"], ncl["closing"]), (0.0, 0.0))
        self.assertEqual((row["net_gain"], row["carried_back"]), (10.0, 0.0))


class TestTheLockedLineOnABooksGain(_Grid):
    """The books show no unused loss, so the line that carried it was dropped
    (TJ-1496)."""

    @rule(R.CA_CARRY_01)
    def test_the_text_says_what_the_lock_holds(self):
        with tempfile.TemporaryDirectory() as td:
            year = _Year(Path(td), 100.0)
            year.close(-100.0)
            text = year.run(year.y, "estimate").stdout
        self.assertRegex(text, r"Locked for 2023:\s+60\.00\s+\(carry forward; "
                               r"40\.00 carried back")


class TestALockTheLedgerRefuses(_Grid):
    """A lock it cannot use gives the row no figure, as the warning says
    (TJ-1486)."""

    def _closed(self, td):
        year = _Year(Path(td), 100.0)
        year.close(-100.0)
        return year

    @rule(R.CA_CARRY_01)
    def test_a_damaged_lock(self):
        with tempfile.TemporaryDirectory() as td:
            year = self._closed(td)
            path = year.y / "filed" / "2023.json"
            doc = json.loads(path.read_text())
            doc["totals"]["realized"] = float("nan")
            path.write_text(json.dumps(doc))
            row = year.row()
        self.assertEqual((row["net_gain"], row["net_source"]),
                         (100.0, "return_row"))

    @rule(R.CA_CARRY_01)
    def test_a_partial_year_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            year = self._closed(td)
            path = year.y / "filed" / "2023.json"
            doc = json.loads(path.read_text())
            doc["closed_at"] = "2023-06-30T12:00:00Z"
            path.write_text(json.dumps(doc))
            row = year.row()
        self.assertEqual((row["net_gain"], row["net_source"]),
                         (100.0, "return_row"))


if __name__ == "__main__":
    unittest.main()

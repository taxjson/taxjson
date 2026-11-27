"""Fix round 7 (wave D) of 2026-10-29, group movesxfer: transfers,
in-kind moves and handoff.

TJ-1054: a .tt sale between two arrivals sold the first arrival's units
          even when the broker had already sold them or the account held
          .tt units of its own, so the second arrival, with no purchase
          left, read as covered and was never booked.
TJ-1036: `tjs journals` listed one ambiguous journal leg once per leg it
          could pair with: the suggestion repeated, the count inflated.
TJ-1037: handoff's own-sale replay left out a split reported in another
          account's books, so a closed-year sale out of the split units
          was not its own row and a same-size January sale read as a double.
TJ-1069: the book cost a rename carries averaged an option's premiums
          where the books pair them oldest first.
TJ-1119: `tjs years` and check-filed read the deleted lock of a year closed
          as a single folder, then migrated, as a year never closed.
TJ-1118: a shared-exports run at a terminal lost its in-memory legacy-id
          migration when it saved an answer, then stopped with a traceback.
TJ-1055: an in-kind contribution of an arrival booked at book value was
          charged to the .tt purchases, so a later arrival's own purchase
          looked spent and that arrival was booked on top of it.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7d_movesxfer
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.transfer_registered import _TAXABLE, _cli, _project, _run

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestRenameCarriesTheClosedContractsPremium(unittest.TestCase):
    """TJ-1069: the carried book cost is what the engine's books carry
    into NEW, with TJ-0852's single-contract case kept
    (tests/features/corporate_actions/test_elect_redo_and_splits_view.py).

    Formerly: test_fix_1029_r7d_movesxfer.TestRenameCarriesTheClosedContractsPremium"""

    PUTS = ("BUYSELL 2025-01-02 10:00:00 SYN250620P00010000{s} -1 {c} 1 100 0\n"
            "BUYSELL 2025-02-03 10:00:00 SYN250620P00010000{s} -1 {c} 5 500 0\n"
            "ASSIGN 2025-06-10 16:00:00 SYN250620P00010000{s} 1 {c} 0 0 0\n"
            "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")
    CALLS = ("BUYSELL 2025-01-02 10:00:00 SYN250620C00010000{s} 1 {c} 1 100 0\n"
             "BUYSELL 2025-02-03 10:00:00 SYN250620C00010000{s} 1 {c} 5 500 0\n"
             "ASSIGN 2025-06-10 16:00:00 SYN250620C00010000{s} -1 {c} 0 0 0\n"
             "ASSIGN 2025-06-10 16:01:00 SYN{s} 100 {c} 10 1000 0\n")

    def _carried(self, td: str, country: str, book: str):
        cur, sfx = ("CAD", ".US") if country == "canada" else ("USD", "")
        root = _project(
            Path(td) / "p", {"margin": _TAXABLE},
            {"inputs/margin/book.tt": book.format(c=cur, s=sfx)
             + f"RENAME 2025-07-02 SYN{sfx} NEW{sfx}\n"}, country=country)
        _run(self, root)
        doc = json.loads(_cli(root, "renames", "--json").stdout)
        wash = root / "work" / "margin_gains_wash.json"
        inv = json.loads((wash if wash.exists()
                          else root / "work" / "margin_gains.json")
                         .read_text())["inventory"]
        held = {x["symbol"]: round(float(x["total_cost"]), 2) for x in inv}
        return (doc["renames"][0]["carried"][0]["book_cost"],
                held[f"NEW{sfx}"])

    def test_carried_cost_is_the_books(self):
        for country, book, want in (("canada", self.PUTS, 900),
                                    ("usa", self.PUTS, 900),
                                    ("usa", self.CALLS, 1100),
                                    ("canada", self.CALLS, 1300)):
            with tempfile.TemporaryDirectory() as td:
                carried, books = self._carried(td, country, book)
                self.assertEqual((carried, books), (want, want),
                                 (country, want))


class TestSharedExportsPromptKeepsTheMigration(unittest.TestCase):
    """TJ-1118: a year folder reading ../inputs, one spin-off's election
    under its legacy id, the other's asked at the terminal. Both are
    booked (as `run --no-input` books the first alone), the shared
    manifest keeps the legacy id (TJ-0848) and takes the new answer.

    Formerly: test_fix_1029_r7d_movesxfer.TestSharedExportsPromptKeepsTheMigration"""

    def test_both_events_are_booked(self):
        import shutil
        from taxjson.lib import corp_actions as ca
        from _support.corp_elect_terminal import _IB_HEADER, _ib_spinoff, _on_a_terminal, _save
        from _support.corp_elect_terminal import _project as _book_project
        with tempfile.TemporaryDirectory() as d:
            top = Path(d) / "taxes"
            year = _book_project(top / "y", {"margin": ""},
                                 settings='inputs_dir = "../inputs"\n')
            year = year.rename(top / "2025")
            shutil.rmtree(year / "inputs" / "margin")
            shared = top / "inputs" / "margin"
            shared.mkdir(parents=True)
            (shared / "book.tt").write_text(
                "BUYSELL 2025-01-02 09:00:00 PARENT.US 10 CAD 10 100 0\n")
            csv_file = shared / "ib.csv"
            csv_file.write_text(_IB_HEADER + _ib_spinoff("2025-06-02",
                                                         "CHILDONE")
                                + _ib_spinoff("2025-07-02", "CHILDTWO"))
            one, two = ca.parse_ib_corporate_actions(csv_file,
                                                     account="margin")
            old = one.legacy_event_id()
            self.assertNotEqual(old, one.event_id)
            path = shared / "elections.json"
            _save(path, {old: ("taxable_deemed_dividend", one.summary(),
                               {"fmv_per_share": 10})})
            keys = [k for k, _ in ca.options_for("canada", "spinoff")]
            n = keys.index("ignore") + 1
            rc, out, err = _on_a_terminal(year, "run",
                                          answers=f"{n}\n\n{n}\n\n")
            self.assertEqual(rc, 0, out[-1500:] + err[-1500:])
            self.assertNotIn("Traceback", err)
            self.assertNotIn(f"event {one.event_id} unresolved", err)
            recs = ca.Manifest.load(path).records
            self.assertEqual(sorted(recs), sorted([old, two.event_id]))
            self.assertEqual(recs[two.event_id].election, "ignore")


if __name__ == "__main__":
    unittest.main()

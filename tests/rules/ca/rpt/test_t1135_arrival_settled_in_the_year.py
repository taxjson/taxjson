"""Fix round 7 (wave F) of 2026-10-30, the closecash group: regressions of
wave E's repairs. A broker sale of units the coverage replay cannot see, a
transit whose arrival settled in the year, a later rebuy at another
broker and an INKIND line check-tt cannot size yet (synthetic data only).

Formerly: test_fix_1029_r7f_closecash"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_t1135_cash import _Q_HDR, _qt, _t1135, _t1135_project, _tt


class TestTJ1285ArrivalWalkedInTheYear(unittest.TestCase):
    """TJ-1285 (a regression of TJ-1185's fix): on trade dates a departure
    settling after the year whose arrival settled in it read as an unknown
    year-end transit; the units are in the receiver's row.

    Formerly: test_fix_1029_r7f_closecash.TestTJ1285ArrivalWalkedInTheYear"""

    def _rep(self, b_rows, b_move, b_bal, b_extra=""):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + b_rows,
                      "c.tt": _tt(0, 0, *b_move, b_bal) + b_extra}},
                tax_date="trade")
            return _t1135(root)

    @rule(R.CA_RPT_20)
    def test_the_whole_move(self):
        rep = self._rep(_qt("2025-12-31", "2025-12-31", "DEP", 100, 99900002),
                        ("2025-12-31", 100), 100)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertEqual(round(sum(p["year_end_cost"] or 0
                                   for p in rep["properties"]
                                   if p.get("cash")), 2), 125.0)

    @rule(R.CA_RPT_20)
    def test_the_part_that_arrived_stays_in_the_receivers_row_only(self):
        rep = self._rep(_qt("2025-12-31", "2025-12-31", "DEP", 60, 99900002)
                        + _qt("2026-01-03", "2026-01-03", "DEP", 40, 99900002),
                        ("2025-12-31", 60), 60,
                        "CASHMOVE 2026-01-03 USD 40 own\n")
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertEqual(sorted(p["year_end_cost"] for p in rep["properties"]
                                if p.get("cash")), [50.0, 75.0])

    def test_settlement_dates_are_unchanged(self):
        # No trade basis: the walk's own close, as before the fix.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2025-12-31", "DEP",
                                            100, 99900002),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 100)}})
            rep = _t1135(root)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))

    def test_a_receiver_in_canada_names_nothing(self):
        # A Canadian receiver: the arrival walked in the year is at b, so
        # nothing is unknown at the year end.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": None}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                            -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2025-12-31", "DEP",
                                            100, 99900002),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 100)}},
                tax_date="trade")
            rep = _t1135(root)
        self.assertTrue(rep["total_settled"], rep.get("cash_not_counted"))
        self.assertEqual(rep["cash_not_counted"], [])


if __name__ == "__main__":
    unittest.main()

"""Regression tests for fix round 7 (wave E), group shorts: a marked short
beside an own move's shares, a written option's premium in the holdings views
under close timing, the run's no-total note and holdings report after a split
one account reports, and edge-cases' HELD DAY 30 for a short holder.

Synthetic data only; account labels and tickers are made up.

Formerly: test_fix_1029_r7e_shorts
"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_edge_analysis import _edge, _t as _edge_row
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestHeldDayThirtyOfAShortHolder(unittest.TestCase):
    """TJ-1260: a holder that is short on day 30 holds no units. Keeps
    TJ-1113 (per holder).

    Formerly: test_fix_1029_r7e_shorts.TestHeldDayThirtyOfAShortHolder"""
    S = "ZZS.TO"

    def _books(self, *extra):
        return {"margin": [
            _edge_row("margin", "BUYSELL", "2024-02-10", self.S, 100, -1000.0),
            _edge_row("margin", "BUYSELL", "2024-03-10", self.S, -120, 960.0),
            *extra]}

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_a_short_holder_shows_none(self):
        _doc, edge, text = _edge(self._books(), year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)
        self.assertRegex(text, r"2024-03-10\s+100\s+0\n")

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_a_short_never_hides_an_in_window_holders_units(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-03-20", self.S, 20, -180.0)]
        _doc, edge, _text = _edge(self._books(), {"tfsa": tfsa}, year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 20.0)

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_an_old_holder_stays_apart(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-01-10", self.S, 50, -450.0)]
        _doc, edge, _text = _edge(self._books(), {"tfsa": tfsa}, year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)

    def _year_end(self, sheltered=None):
        books = {"margin": [
            _edge_row("margin", "BUYSELL", "2024-12-02", self.S, 100, -1000.0),
            _edge_row("margin", "BUYSELL", "2024-12-20", self.S, -120, 960.0),
            _edge_row("margin", "BUYSELL", "2025-01-06", self.S, 5, -40.0)]}
        _doc, edge, _text = _edge(books, sheltered, year=2024)
        (r,) = edge["year_boundary"]["loss_windows"]
        return r

    @rule(R.CA_RPT_07, R.CA_SL_02)
    def test_allowed_reason_of_a_short_holder(self):
        r = self._year_end()
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("no units were held at the end of day 30", r["why"])
        self.assertNotIn("-15", r["why"])
        self.assertNotIn("another holder", r["why"])

    @rule(R.CA_RPT_07, R.CA_SL_02)
    def test_another_holders_units_are_named_in_full(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-01-10", self.S, 50, -450.0)]
        r = self._year_end({"tfsa": tfsa})
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("50 held by another holder", r["why"])


if __name__ == '__main__':
    unittest.main()

"""Batch C (cash books, the FX-on-cash ledger v2, the generic importer,
ALLOWLOSS): 2026-10 audit findings 51, 52, 53, 70, 87. All data
synthetic.

Formerly: test_fix_batch_c_cash"""
import unittest

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as V2


def _line(text, book="margin/tt"):
    e = CL.parse_line(text)
    e.update(book=book, label="margin", where="c.tt:1")
    return e


def _build(lines, rate_of=lambda c, d: 1.5):
    return V2.build([], {"lines": lines, "events": [], "problems": []},
                    "CAD", 2025, rate_of, country="canada")


class TestCashBookNames(unittest.TestCase):
    """#51: two accounts whose hashes share the first 4 hex were one
    book (`margin/ib:3135`) that no `at=` could name.

    Formerly: test_fix_batch_c_cash.TestCashBookNames"""

    A, B, C = "3135769a44", "31355d479e", "7f20aa0b11"   # synthetic

    def test_colliding_prefixes_get_longer_names(self):
        bk = CE.Books()
        bk.add("margin", "ib", self.A)
        bk.add("margin", "ib", self.B)
        names = bk.all_names("margin")
        self.assertEqual(names, ["margin/ib:31355", "margin/ib:31357"])
        self.assertEqual(bk.name("margin", "ib", self.A), "margin/ib:31357")
        self.assertEqual(bk.for_at("margin", "ib:31357"),
                         ("margin/ib:31357", ""))
        b, why = bk.for_at("margin", "ib:3135")
        self.assertIsNone(b)
        self.assertIn("margin/ib:31355", why)

    def test_distinct_prefixes_keep_four(self):
        # A project without a collision keeps its book names (and so its
        # at= lines and the prior year's recorded close).
        bk = CE.Books()
        bk.add("margin", "ib", self.A)
        bk.add("margin", "ib", self.C)
        self.assertEqual(bk.all_names("margin"),
                         ["margin/ib:3135", "margin/ib:7f20"])
        one = CE.Books()
        one.add("margin", "ib", self.A)
        self.assertEqual(one.all_names("margin"), ["margin/ib"])


class TestCashOpenNoBook(unittest.TestCase):
    """#52: a CASHOPEN whose book could not be told was dropped without
    a word.

    Formerly: test_fix_batch_c_cash.TestCashOpenNoBook"""

    def test_reported(self):
        ln = _line("CASHOPEN 2025-01-01 USD 100 140", None)
        ln["book_problem"] = ("its account folder holds several books "
                              "(margin/ib:3135, margin/ib:7f20) — add "
                              "at=<book>, or a CASHBOOK <book> line to the "
                              "file")
        r = _build([ln])
        self.assertEqual(r["status"], "not_computed")
        texts = [p["text"] for p in r["problems"]]
        self.assertTrue(any(t.startswith("c.tt:1: its account folder "
                                         "holds several books")
                            for t in texts), texts)

    def test_base_currency_open_still_ignored(self):
        r = _build([_line("CASHOPEN 2025-01-01 CAD 100 100", None)])
        self.assertEqual(r["status"], "computed")
        self.assertEqual(r["problems"], [])


class TestExplicitValueNeedsNoRate(unittest.TestCase):
    """#53: FXCONV USD -> EUR with value= was refused for want of a USD
    rate it does not use.

    Formerly: test_fix_batch_c_cash.TestExplicitValueNeedsNoRate"""

    def _lines(self):
        return [_line("CASHOPEN 2025-01-01 USD 100 140"),
                _line("CASHOPEN 2025-01-01 EUR 0 0"),
                _line("FXCONV 2025-02-03 USD 100 EUR 90 value=150"),
                _line("CASHBAL 2025-12-31 USD 0"),
                _line("CASHBAL 2025-12-31 EUR 90")]

    def test_no_rate_on_file(self):
        r = _build(self._lines(), rate_of=lambda c, d: None)
        self.assertEqual(r["status"], "computed", r["problems"])
        self.assertAlmostEqual(r["net_gain"], 10.0)     # 150 - 140
        self.assertEqual(r["close"]["books"]["margin/tt"]["EUR"],
                         {"units": 90.0, "cost": 150.0})

    def test_same_figures_with_a_rate(self):
        r = _build(self._lines(), rate_of=lambda c, d: 1.4)
        self.assertEqual(r["status"], "computed", r["problems"])
        self.assertAlmostEqual(r["net_gain"], 10.0)
        self.assertEqual(r["events"][0]["rate"], 1.4)

    def test_without_value_a_rate_is_still_needed(self):
        lines = self._lines()
        lines[2] = _line("FXCONV 2025-02-03 USD 100 EUR 90")
        r = _build(lines, rate_of=lambda c, d: None)
        self.assertEqual(r["status"], "not_computed")


if __name__ == "__main__":
    unittest.main()

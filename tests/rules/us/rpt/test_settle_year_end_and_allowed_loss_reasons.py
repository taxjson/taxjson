"""The 2026-10-29 fix round, group "edgecases": `tjs edge-cases`
and `tjs option-boundary` on synthetic books.

- TJ-0396: a contract on explicit close timing (option_premium_timing =
  "close") whose write year's lock records grant timing, or reports the
  premium, is a double count, flagged ATTENTION like a transition
  contract's (CA-OPT-07).
- TJ-0225: edge-cases names a lock reached through [settings]
  prior_year_record by its path, as option-boundary does, not as
  filed/<year>.json.

Made-up symbols (ZZQ, Q) and amounts only.

Formerly: test_fix_1029_edgecases
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _support.us_edge_cases import _edge as _us_edge, _t as _ut

from taxjson.lib.edge_cases import render_text


@rule(R.US_RPT_13)
class TestShortDeferralWording(unittest.TestCase):
    """TJ-0342: a short covered at a loss and re-shorted in the window
    carries the denial in the replacement short's proceeds (Reg.
    §1.1091-1(g)); edge-cases worded it as the cost of units held.

    Formerly: test_fix_1029_edgecases.TestShortDeferralWording"""

    SHORT = [_ut("margin", "2025-11-03", -10, 1000),
             _ut("margin", "2025-11-17", 10, -1500),
             _ut("margin", "2025-11-20", -10, 1000)]

    def test_short_rows_say_proceeds_and_covered(self):
        _doc, edge, text = _us_edge({"margin": self.SHORT})
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual((r["qty"], r["deferred"]), (-10.0, 500.0))
        self.assertEqual(
            r["why"], "500.00 of denied losses sits in the proceeds of the "
            "10 units still short at the end of 2025; it reduces the gain "
            "(or grows the loss) of the year they are covered")
        self.assertNotIn("-10 units", r["why"])
        self.assertIn(r["why"], " ".join(text.split()))
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn("negative UNITS: the proceeds of units still short",
                      " ".join(default.split()))

    def test_long_rows_are_unchanged(self):
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2025-11-03", 10, -1000),
            _ut("margin", "2025-11-17", -10, 500),
            _ut("margin", "2025-11-20", 10, -500)]})
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(
            r["why"], "500.00 of denied losses sits in the cost of the 10 "
            "units still held at the end of 2025; it reduces the gain (or "
            "grows the loss) of the year they are sold")
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn("DEFERRED: denied losses in the cost of units still "
                      "held; they lower the gain when sold.",
                      " ".join(default.split()))


def _deferred(edge):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"], r["why"])
            for r in edge["year_boundary"]["deferred_at_year_end"]]


# A 500.00 loss on 2025-11-17 disallowed by the 10 shares bought back on
# 2025-11-20 (§1091(d)), which are sold on Dec 31: the trade settles
# 2026-01-02.
WASHED_DEC31 = [_ut("margin", "2025-11-03", 10, -1000),
                _ut("margin", "2025-11-17", -10, 500),
                _ut("margin", "2025-11-20", 10, -500),
                _ut("margin", "2025-12-31", -10, 700, settle="2026-01-02")]


@rule(R.US_RPT_13)
class TestUSSettleYearEnd(unittest.TestCase):
    """TJ-0343: a US project on tax_date = "settle" reads the lots at
    the end of Dec 31 on settlement dates: a sale traded Dec 31 that
    settles in January is a next-year disposition, and the disallowed
    loss in the basis of the lots it sells goes into that return.

    Formerly: test_fix_1029_edgecases.TestUSSettleYearEnd"""

    def test_a_december_31_sale_settling_in_january(self):
        doc, edge, text = _us_edge({"margin": WASHED_DEC31},
                                   tax_date="settle")
        self.assertEqual(doc["inventory_year_end_basis"], "settle")
        ((acct, qty, dw, as_of, why),) = _deferred(edge)
        self.assertEqual((acct, qty, dw, as_of),
                         ("margin", 10.0, 500.0, "2025-12-31"))
        self.assertIn("500.00 of denied losses sits in the cost of the 10 "
                      "units still held at the end of 2025; it reduces",
                      why)
        self.assertNotIn("(by ", why)
        self.assertIn(why, " ".join(text.split()))
        # The straddle section still lists the sale.
        self.assertEqual(
            [(r["symbol"], r["lands_in"])
             for r in edge["year_boundary"]["straddles"]],
            [("ZZQ.US", 2026)])

    def test_on_trade_dates_the_sale_is_this_years(self):
        # The same books on trade dates: the sale is a 2025 disposition,
        # its deferral used in the 2025 return; nothing is carried.
        doc, edge, _text = _us_edge({"margin": WASHED_DEC31})
        self.assertNotIn("inventory_year_end_basis", doc)
        self.assertEqual(_deferred(edge), [])

    def test_a_replacement_settling_in_january_is_not_held(self):
        # The replacement trades Dec 31 and settles 2026-01-02: on
        # settlement dates it was not held at the end of 2025 (as a
        # January replacement; the window across Dec 31 lists it).
        rows = [_ut("margin", "2025-11-03", 10, -1000),
                _ut("margin", "2025-12-15", -10, 500),
                _ut("margin", "2025-12-31", 10, -500, settle="2026-01-02")]
        _doc, settle, _ = _us_edge({"margin": rows}, tax_date="settle")
        self.assertEqual(_deferred(settle), [])
        _doc, trade, _ = _us_edge({"margin": rows})
        self.assertEqual([r[:4] for r in _deferred(trade)],
                         [("margin", 10.0, 500.0, "2025-12-31")])

    def test_blended_accounts_keep_the_basis(self):
        doc, edge, _text = _us_edge({
            "margin": WASHED_DEC31,
            "margin2": [_ut("margin2", "2025-06-02", 5, -500)]},
            tax_date="settle")
        self.assertEqual([r[:4] for r in _deferred(edge)],
                         [("margin", 10.0, 500.0, "2025-12-31")])
        self.assertNotIn("(by ", _deferred(edge)[0][4])

    def test_an_older_gains_file_says_trade_date(self):
        # A gains file written before the fix has no
        # inventory_year_end_basis: its year end is the trade-date
        # walk's, and the settle project is told so.
        doc, _edge, _text = _us_edge({"margin": WASHED_DEC31[:3]},
                                     tax_date="settle")
        doc.pop("inventory_year_end_basis")
        from taxjson.lib.edge_cases import analyze
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in WASHED_DEC31[:3]]}))
            (root / "work" / "margin_gains_wash.json").write_text(
                json.dumps(doc))
            edge = analyze(root, {
                "settings": {"year": 2025, "country": "usa",
                             "tax_date": "settle"},
                "accounts": {"margin": {"type": "taxable"}}})
        ((_a, _q, _dw, _as_of, why),) = _deferred(edge)
        self.assertIn("still held at the end of 2025 (by trade date);", why)


def _windows(edge, sym="ZZQ.US"):
    return [(w["loss_date"], w["allowed_by"], w["why"])
            for w in edge["year_boundary"]["loss_windows"]
            if w["symbol"] == sym]


@rule(R.US_RPT_05)
class TestUSAllowedReason(unittest.TestCase):
    """TJ-0286: a US loss across Dec 31 with nothing disallowed said "it
    was the lot sold, or an earlier loss used it" for every loss,
    unchecked. Now each purchase in the window is accounted for from
    the books, or the text claims no reason.

    Formerly: test_fix_1029_edgecases.TestUSAllowedReason"""

    def test_the_purchase_is_the_lot_sold(self):
        _doc, edge, text = _us_edge({"margin": [
            _ut("margin", "2024-12-20", 10, -1000),
            _ut("margin", "2025-01-10", -10, 500)]})
        ((day, by, why),) = _windows(edge)
        self.assertEqual((day, by), ("2025-01-10", "lot_sold"))
        self.assertTrue(why.endswith(
            "allowed: none of those purchases is its replacement (margin "
            "2024-12-20 +10 is the lot sold)"), why)
        self.assertNotIn("or an earlier loss used it", text)

    def test_one_sale_of_two_lots(self):
        # One sale is a row per lot: the December lot is sold by the
        # same sale as the November one.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2024-11-01", 10, -1000),
            _ut("margin", "2024-12-20", 5, -500),
            _ut("margin", "2025-01-10", -15, 600)]})
        rows = _windows(edge)
        self.assertEqual([by for _d, by, _w in rows],
                         ["lot_sold", "lot_sold"])
        self.assertIn("(margin 2024-12-20 +5 is the lot sold)", rows[0][2])

    def test_the_purchase_replaced_another_loss(self):
        # 2026-01-05's 10 shares replace the 2025-12-10 loss (its wash
        # sale names them), so the 2025-12-20 loss has none left.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2025-11-01", 20, -2000),
            _ut("margin", "2025-12-10", -10, 500),
            _ut("margin", "2025-12-20", -10, 500),
            _ut("margin", "2026-01-05", 10, -500)]})
        rows = dict((d, (by, why)) for d, by, why in _windows(edge))
        self.assertIsNone(rows["2025-12-10"][0])            # denied
        by, why = rows["2025-12-20"]
        self.assertEqual(by, "replacement_used")
        self.assertTrue(why.endswith(
            "allowed: none of those purchases is its replacement (margin "
            "2026-01-05 +10 replaced the 2025-12-10 loss)"), why)

    def test_a_reason_the_books_do_not_hold_is_not_claimed(self):
        # The 2024-12-22 purchase replaced a 2024 loss, which is not in
        # this year's books: no reason is claimed.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2024-11-01", 20, -2000),
            _ut("margin", "2024-12-15", -10, 500),
            _ut("margin", "2024-12-22", 10, -500),
            _ut("margin", "2025-01-08", -10, 500)]})
        ((day, by, why),) = _windows(edge)
        self.assertEqual((day, by), ("2025-01-08", "unknown"))
        self.assertTrue(why.endswith(
            "no disallowance applied by the engine (the books do not "
            "record why margin 2024-12-22 +10 is not its replacement)"), why)
        self.assertNotIn("lot sold", why)


if __name__ == "__main__":
    unittest.main()

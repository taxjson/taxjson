"""TJ-4984: a US year end read on settlement dates (tax_date = "settle")
holds every row dated and settled by Dec 31, whatever row that settles
in January stands before it in the walk's trade-date order.

Made-up symbols (ZZQ, ZZR, ZZS, ZZW) and amounts only.
"""
import unittest

from tax_rules import rule
from taxjson.model.transaction import TaxTransaction
from taxjson.rules import ids as R

from _support.us_edge_cases import _edge as _us_edge, _t as _ut


def _deferred(edge):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"], r["why"])
            for r in edge["year_boundary"]["deferred_at_year_end"]]


def _us_engine(rows, **kw):
    """The US engine on `rows` with the year end and the lots read at
    2025-12-31: (inventory_year_end, lots_at, stderr), the rows as
    (symbol, units, basis, wash deferred)."""
    import contextlib
    import io
    from taxjson.engine.us.taxrules import USATaxRules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = USATaxRules().compute_gains(
            list(rows), per_account_basis=True, year_end="2025-12-31",
            lots_at="2025-12-31", **kw)
    return ([(h["symbol"], h["qty"], h["total_cost"], h["deferred_wash"])
             for h in r["inventory_year_end"]],
            [(x["symbol"], x["date"], x["qty"], x["basis"], x["deferred"])
             for x in r["lots_at"]], err.getvalue())


_SETTLE = {"year_end_basis": "settle", "lots_at_basis": "settle"}
# 10 ZZQ.US sold on Dec 29, the sale settling 2026-01-02: next year's
# disposition on settlement dates, and the first row past the year end
# there. The rows after it in trade order settle in the year.
_STRADDLE = [_ut("margin", "2025-01-06", 10, -100),
             _ut("margin", "2025-12-29", -10, 120, settle="2026-01-02")]


@rule(R.US_RPT_13)
class TestUSSettleYearEndRowsPastTheFirstJanuarySettlement(
        unittest.TestCase):
    """TJ-4984: on settlement dates the year-end lots (and `lots_at`, the
    closed year's per-lot detail) were cut at the first row settling
    after Dec 31, so every row traded after it and settled in the year
    was left out, silently: a purchase, a split, a wash sale's basis
    add."""

    def test_a_purchase_settled_in_the_year_is_held(self):
        rows = _STRADDLE + [_ut("margin", "2025-12-30", 10, -200,
                                sym="ZZR.US")]
        ye, lots, err = _us_engine(rows, **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0),
                              ("ZZR.US", 10.0, 200.0, 0.0)])
        self.assertEqual(lots, [
            ("ZZQ.US", "2025-01-06", 10.0, 100.0, 0.0),
            ("ZZR.US", "2025-12-30", 10.0, 200.0, 0.0)])
        self.assertEqual(err, "")
        # On trade dates (the walk's own order) the sale is the year's.
        ye, lots, _ = _us_engine(rows)
        self.assertEqual(ye, [("ZZR.US", 10.0, 200.0, 0.0)])
        self.assertEqual([x[0] for x in lots], ["ZZR.US"])

    def test_a_split_and_a_wash_sale_after_it_are_in_the_lots(self):
        from taxjson.model.transaction import TaxTransaction
        split = TaxTransaction(
            account="margin", action="SPLIT", date="2025-12-30",
            date_settle="2025-12-30", time="10:00:00", symbol="ZZS.US",
            quantity=2.0, net_amount=0.0, currency="USD", id="split-zzs")
        rows = _STRADDLE + [
            _ut("margin", "2025-02-03", 10, -100, sym="ZZS.US"), split,
            # a 500.00 loss on Dec 30, bought back Dec 31 (§1091(d))
            _ut("margin", "2025-11-03", 10, -1000, sym="ZZW.US"),
            _ut("margin", "2025-12-30", -10, 500, sym="ZZW.US"),
            _ut("margin", "2025-12-31", 10, -500, sym="ZZW.US")]
        ye, lots, err = _us_engine(rows, **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0),
                              ("ZZS.US", 20.0, 100.0, 0.0),
                              ("ZZW.US", 10.0, 1000.0, 500.0)])
        self.assertEqual(lots, [
            ("ZZQ.US", "2025-01-06", 10.0, 100.0, 0.0),
            ("ZZS.US", "2025-02-03", 20.0, 100.0, 0.0),
            ("ZZW.US", "2025-12-31", 10.0, 1000.0, 500.0)])
        self.assertEqual(err, "")
        # `tjs edge-cases`: the disallowed loss is carried into 2026.
        _doc, edge, _text = _us_edge({"margin": rows}, tax_date="settle")
        self.assertEqual([r[:4] for r in _deferred(edge)],
                         [("margin", 10.0, 500.0, "2025-12-31")])

    _SAID = ("warning: ATTENTION: year end: margin: {}: the lots at the end of 2025-12-31 (by "
             "settlement date) are not exact")

    def _crossed(self, later, names="ZZQ.US"):
        # The position stays as it stood before the January-settling
        # sale and one warning names it (once, for the year end and
        # `lots_at`): headline, then its detail lines.
        ye, lots, err = _us_engine(_STRADDLE + later, **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0)])
        self.assertEqual(lots, [("ZZQ.US", "2025-01-06", 10.0, 100.0, 0.0)])
        self.assertEqual(err.count("warning: "), 1, err)
        head, why, check = err.rstrip("\n").split("\n")
        self.assertEqual(head, self._SAID.format(names))
        self.assertTrue(why.startswith(
            "  A row that settles after that day stands before 1 day(s) "
            "of rows settled by it (2025-12-30) in trade order, and both "
            "touch this account's position"), why)
        self.assertIn("the later rows are not in them.", why)
        self.assertTrue(check.startswith("  Check these lots"), check)

    def test_one_position_crossed_by_both_orders_is_said(self):
        # The same security bought again on Dec 30, settled that day:
        # the lots the January-settling sale drew are gone from the walk.
        self._crossed([_ut("margin", "2025-12-30", 5, -60)])

    def _row(self, **kw):
        from taxjson.model.transaction import TaxTransaction
        return TaxTransaction(
            account="margin", action="SPLIT", date="2025-12-30",
            date_settle="2025-12-30", time="10:00:00", symbol="ZZQ.US",
            net_amount=0.0, currency="USD", id="event-zzq", **kw)

    def test_a_split_of_the_position_the_sale_emptied_is_said(self):
        # Review, 4: the split changes no lot in the walk (the sale drew
        # them all), and on settlement dates the 10 shares became 20.
        self._crossed([self._row(quantity=2.0)])

    def test_a_ticker_change_of_it_names_both_symbols(self):
        self._crossed([self._row(quantity=1.0, symbol_new="ZZV.US")],
                      "ZZQ.US, ZZV.US")

    def test_a_partial_sale_then_a_ticker_change_counts_no_unit_twice(self):
        # Review, 4: 4 of 10 sold (settling in January), then the change:
        # the walk's 6 renamed units were listed beside the 10.
        rows = [_ut("margin", "2025-01-06", 10, -100),
                _ut("margin", "2025-12-29", -4, 60, settle="2026-01-02"),
                self._row(quantity=1.0, symbol_new="ZZV.US")]
        ye, _lots, err = _us_engine(rows, **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0)])
        self.assertIn(self._SAID.format("ZZQ.US, ZZV.US"), err)

    def test_another_accounts_lots_of_the_symbol_are_exact(self):
        # Review 2 (U9): account second holds 4 ZZQ with no crossing
        # row; the 2-for-1 split of Dec 30 reaches both accounts. Its
        # lots are split (8 units); only margin's position, which the
        # January-settling sale emptied, is said.
        rows = _STRADDLE + [
            _ut("second", "2025-02-03", 4, -40),
            self._row(quantity=2.0),
            TaxTransaction(
                account="second", action="SPLIT", date="2025-12-30",
                date_settle="2025-12-30", time="10:00:00", symbol="ZZQ.US",
                quantity=2.0, net_amount=0.0, currency="USD",
                id="event-zzq-second")]
        ye, _lots, err = _us_engine(rows, **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0),
                              ("ZZQ.US", 8.0, 40.0, 0.0)])
        self.assertEqual(err.count("warning: "), 1, err)
        self.assertIn(self._SAID.format("ZZQ.US"), err)

    def test_a_dividend_on_the_sold_position_is_no_crossing(self):
        from taxjson.model.transaction import TaxTransaction
        div = TaxTransaction(
            account="margin", action="DIVIDEND", date="2025-12-30",
            date_settle="2025-12-30", time="10:00:00", symbol="ZZQ.US",
            quantity=0.0, net_amount=3.0, currency="USD", id="div-zzq")
        ye, _lots, err = _us_engine(
            _STRADDLE + [div, _ut("margin", "2025-12-30", 10, -200,
                                  sym="ZZR.US")], **_SETTLE)
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0, 0.0),
                              ("ZZR.US", 10.0, 200.0, 0.0)])
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()

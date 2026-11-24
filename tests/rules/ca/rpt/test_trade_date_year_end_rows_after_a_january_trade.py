"""TJ-4984: a Canadian year end read on trade dates (tax_date = "trade")
holds every row traded by Dec 31, whatever row traded in January stands
before it in the walk's settlement-date order.

Made-up symbols (ZZY, ZZJ, ZZS, ZZT, ZZW) and amounts only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from _support.ca_edge_cases import STOCK as CA_STOCK, _edge as _ca_edge, _t as _ct


def _ca_engine(rows, **kw):
    """The Canadian engine on `rows` with the year end read at
    2025-12-31: (inventory_year_end as (symbol, units, cost, denied
    losses in it), stderr)."""
    import contextlib
    import io
    from taxjson.engine.ca.taxrules import CanadaTaxRules
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = CanadaTaxRules().compute_gains(list(rows),
                                           year_end="2025-12-31", **kw)
    return ([(h["symbol"], h["qty"], h["total_cost"], h["deferred_wash"])
             for h in r["inventory_year_end"]], err.getvalue())


# 10 ZZY.US sold on Dec 29, the sale settling 2026-01-05; a purchase of
# another security traded and settled 2026-01-02 stands before it in the
# walk (settlement order) and is the first row past the year end on
# trade dates.
_SOLD = [_ct("margin", "2025-06-02", "2025-06-03", CA_STOCK, 10, -100),
         _ct("margin", "2025-12-29", "2026-01-05", CA_STOCK, -10, 120)]
_JANUARY = _ct("margin", "2026-01-02", "2026-01-02", "ZZJ.TO", 5, -50)


@rule(R.CA_RPT_07)
class TestCanadaTradeYearEndRowsPastTheFirstJanuaryTrade(unittest.TestCase):
    """TJ-4984: on trade dates the year-end pools were cut at the first
    row traded after Dec 31 in the walk's settlement order, so every
    row traded in the year and settling after it was left out, silently:
    a sale (its units still shown as held), a purchase, a split, a
    denied loss's addition."""

    def test_the_december_sale_has_left_the_pool(self):
        ye, err = _ca_engine(_SOLD + [_JANUARY], year_end_basis="trade")
        self.assertEqual((ye, err), ([], ""))
        # A January row that moves no pool cut the year short too.
        from taxjson.model.transaction import TaxTransaction
        div = TaxTransaction(
            account="margin", action="DIVIDEND", date="2026-01-02",
            date_settle="2026-01-02", time="10:00:00", symbol="ZZJ.TO",
            quantity=0.0, net_amount=3.0, currency="CAD", id="div-zzj")
        ye, _ = _ca_engine(_SOLD + [div], year_end_basis="trade")
        self.assertEqual(ye, [])
        # On settlement dates (the walk's own) it is next year's sale.
        ye, _ = _ca_engine(_SOLD + [_JANUARY])
        self.assertEqual(ye, [(CA_STOCK, 10.0, 100.0, 0.0)])

    def test_a_purchase_a_split_and_a_denied_loss_after_it_are_in(self):
        from taxjson.model.transaction import TaxTransaction
        split = TaxTransaction(
            account="margin", action="SPLIT", date="2025-12-30",
            date_settle="2026-01-05", time="10:00:00", symbol="ZZS.TO",
            quantity=2.0, net_amount=0.0, currency="CAD", id="split-zzs")
        rows = [_JANUARY,
                _ct("margin", "2025-12-29", "2026-01-05", "ZZT.TO", 4, -40),
                _ct("margin", "2025-03-03", "2025-03-04", "ZZS.TO", 10,
                    -100), split,
                # a 250.00 loss on Dec 29, 5 bought back Dec 30: denied
                _ct("margin", "2025-03-03", "2025-03-04", "ZZW.TO", 10,
                    -1000),
                _ct("margin", "2025-12-29", "2026-01-05", "ZZW.TO", -5, 250),
                _ct("margin", "2025-12-30", "2026-01-06", "ZZW.TO", 5,
                    -250)]
        ye, err = _ca_engine(rows, year_end_basis="trade")
        self.assertEqual(sorted(ye), [("ZZS.TO", 20.0, 100.0, 0.0),
                                      ("ZZT.TO", 4.0, 40.0, 0.0),
                                      ("ZZW.TO", 10.0, 1000.0, 250.0)])
        self.assertEqual(err, "")
        # `tjs edge-cases`: the denied loss is carried into 2026.
        _doc, edge, _ = _ca_edge({"margin": rows}, tax_date="trade")
        self.assertEqual(
            [(r["account"], r["qty"], r["deferred"], r["as_of"])
             for r in edge["year_boundary"]["deferred_at_year_end"]],
            [("margin", 10.0, 250.0, "2025-12-31")])

    _SAID = ("warning: ATTENTION: year end: {}: the pool at the end of 2025-12-31 (by trade "
             "date) is not exact")

    def test_one_pool_crossed_by_both_orders_is_said(self):
        # The January purchase is of the same security: 5 of the 10
        # units leave at the pool's average before it (10.00), and a
        # warning names the pool, once: headline, then its detail lines.
        rows = [_ct("margin", "2025-06-02", "2025-06-03", CA_STOCK, 10,
                    -100),
                _ct("margin", "2025-12-29", "2026-01-05", CA_STOCK, -5,
                    100),
                _ct("margin", "2026-01-02", "2026-01-02", CA_STOCK, 10,
                    -200)]
        ye, err = _ca_engine(rows, year_end_basis="trade")
        self.assertEqual(ye, [(CA_STOCK, 5.0, 50.0, 0.0)])
        self.assertEqual(err.count("is not exact"), 1, err)
        head, why, check = [x for x in err.rstrip("\n").split("\n")
                            if "not exact" in x or x.startswith("  ")]
        self.assertEqual(head, self._SAID.format(CA_STOCK))
        self.assertTrue(why.startswith(
            "  A row traded after that day settles before a row traded "
            "by it, and both touch this pool"), why)
        self.assertIn("where a split or a ticker change is among the "
                      "crossing rows the units are not exact either.", why)
        self.assertTrue(check.startswith("  Check this year-end pool"))

    def test_a_split_between_the_sales_trade_and_settlement_is_said(self):
        # Review, 5: a 2-for-1 split traded Jan 2 inside the lag of a
        # Dec 29 sale of 4 (booked as 8 new units): on trade dates 6
        # units are held at the year end; the figure shown is not that,
        # and the warning says the units are not exact.
        from taxjson.model.transaction import TaxTransaction
        split = TaxTransaction(
            account="margin", action="SPLIT", date="2026-01-02",
            date_settle="2026-01-02", time="10:00:00", symbol=CA_STOCK,
            quantity=2.0, net_amount=0.0, currency="CAD", id="split-zzy")
        rows = [_ct("margin", "2025-06-02", "2025-06-03", CA_STOCK, 10,
                    -100),
                _ct("margin", "2025-12-29", "2026-01-05", CA_STOCK, -4, 60),
                split]
        ye, err = _ca_engine(rows, year_end_basis="trade")
        self.assertEqual(ye, [(CA_STOCK, 2.0, 20.0, 0.0)])
        self.assertIn(self._SAID.format(CA_STOCK), err)
        self.assertIn("the units are not exact either", err)

    def test_a_split_of_a_pool_the_january_sale_emptied_is_said(self):
        # The pool is sold out on Jan 2 (settled that day); its split
        # traded Dec 30 settles after: nothing left to split in the
        # walk, 20 units on trade dates.
        from taxjson.model.transaction import TaxTransaction
        split = TaxTransaction(
            account="margin", action="SPLIT", date="2025-12-30",
            date_settle="2026-01-05", time="10:00:00", symbol=CA_STOCK,
            quantity=2.0, net_amount=0.0, currency="CAD", id="split-zzy")
        rows = [_ct("margin", "2025-06-02", "2025-06-03", CA_STOCK, 10,
                    -100),
                _ct("margin", "2026-01-02", "2026-01-02", CA_STOCK, -10,
                    150), split]
        ye, err = _ca_engine(rows, year_end_basis="trade")
        self.assertIn(self._SAID.format(CA_STOCK), err)
        self.assertEqual(ye, [(CA_STOCK, 10.0, 100.0, 0.0)])


if __name__ == "__main__":
    unittest.main()

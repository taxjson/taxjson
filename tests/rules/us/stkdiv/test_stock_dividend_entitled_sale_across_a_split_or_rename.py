"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7b_coreus
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


def _inv(res):
    return {(i["account"], i["symbol"]): (round(i["qty"], 6),
                                          round(i["total_cost"], 4),
                                          i["holding_start_date"])
            for i in res["inventory"]}


# ------------------------------------------------------- TJ-0647, TJ-0648
class TestStockDividendSaleThroughSplitOrRename(unittest.TestCase):
    """100 bought for 1000; ex-date 2025-06-02; 50 sold 2025-06-03 for
    600. With a 2:1 split on 2025-06-05 and 20 new shares paid on
    2025-06-16, the 200 entitled post-split shares become 220: the sale
    keeps 1000 / 2 / 1.1 of cost (gain 145.4545) and the 120 held cost
    545.4545, as when the split comes before the sale. With a rename
    instead and 10 new shares, the same.

    Formerly: test_fix_1029_r7b_coreus.TestStockDividendSaleThroughSplitOrRename"""

    def _book(self, split):
        book = [tx("BUYSELL", "2025-01-06", "ZQS.US", 100, 1000.0),
                tx("BUYSELL", "2025-06-03", "ZQS.US", -50, 600.0)]
        if split:
            book += [tx("SPLIT", "2025-06-05", "ZQS.US", 2),
                     tx("BUYSELL", "2025-06-16", "ZQS.US", 20, 0.0,
                        type="stock_dividend", ex_date="2025-06-02")]
        else:
            book += [tx("SPLIT", "2025-06-05", "ZQS.US", 1,
                        symbol_new="ZQN.US"),
                     tx("BUYSELL", "2025-06-16", "ZQN.US", 10, 0.0,
                        type="stock_dividend", ex_date="2025-06-02")]
        return book

    @rule(R.US_STKDIV_05)
    def test_split_between_the_sale_and_the_pay_date(self):
        res = _gains(self._book(split=True))
        sale = _sales(res)[0]
        self.assertAlmostEqual(sale["cost"], 454.5455, places=3)
        self.assertAlmostEqual(sale["gain"], 145.4545, places=3)
        self.assertEqual(_inv(res)[("margin", "ZQS.US")],
                         (120.0, 545.4545, "2025-01-06"))

    @rule(R.US_STKDIV_05)
    def test_rename_between_the_sale_and_the_pay_date(self):
        res = _gains(self._book(split=False))
        sale = _sales(res)[0]
        self.assertEqual(sale["symbol"], "ZQS.US")
        self.assertAlmostEqual(sale["cost"], 454.5455, places=3)
        self.assertAlmostEqual(sale["gain"], 145.4545, places=3)
        self.assertEqual(_inv(res)[("margin", "ZQN.US")],
                         (60.0, 545.4545, "2025-01-06"))

    @rule(R.US_STKDIV_05)
    def test_another_security_sold_under_the_old_ticker_later(self):
        """A sale of the old ticker AFTER the rename is not the renamed
        security's: it keeps its own cost."""
        book = [tx("BUYSELL", "2025-01-06", "ZQS.US", 100, 1000.0),
                tx("SPLIT", "2025-05-29", "ZQS.US", 1, symbol_new="ZQN.US"),
                tx("BUYSELL", "2025-05-30", "ZQS.US", 10, 50.0),
                tx("BUYSELL", "2025-06-03", "ZQS.US", -10, 60.0),
                tx("BUYSELL", "2025-06-16", "ZQN.US", 10, 0.0,
                   type="stock_dividend", ex_date="2025-06-02")]
        res = _gains(book)
        sale = _sales(res, symbol="ZQS.US")[0]
        self.assertAlmostEqual(sale["cost"], 50.0, places=4)
        self.assertEqual(_inv(res)[("margin", "ZQN.US")],
                         (110.0, 1000.0, "2025-01-06"))


if __name__ == "__main__":
    unittest.main()

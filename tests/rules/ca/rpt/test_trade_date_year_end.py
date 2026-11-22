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
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from _support.ca_edge_cases import (
    DENIED as CA_DENIED, STOCK as CA_STOCK, _edge as _ca_edge, _t as _ct,
)


@rule(R.CA_RPT_07)
class TestCanadaTradeYearEnd(unittest.TestCase):
    """TJ-0343's mirror: a Canadian project on tax_date = "trade" reads
    the pools at the end of Dec 31 on trade dates. A sale traded Dec 31
    that settles in January is this year's disposition there; the
    deferral it released went into this year's return, not the next.

    Formerly: test_fix_1029_edgecases.TestCanadaTradeYearEnd"""

    # 250.00 denied on 2025-11-17 (the other 5 units held on day 30).
    DEC31 = CA_DENIED + [_ct("margin", "2025-12-31", "2026-01-02",
                             CA_STOCK, -5, 400)]

    def _rows(self, edge):
        return [(r["account"], r["qty"], r["deferred"], r["as_of"])
                for r in edge["year_boundary"]["deferred_at_year_end"]]

    def test_the_december_31_sale_has_left_the_pool(self):
        doc, edge, _ = _ca_edge({"margin": self.DEC31}, tax_date="trade")
        self.assertEqual(doc["inventory_year_end_basis"], "trade")
        self.assertEqual(self._rows(edge), [])
        # On settlement dates (the CRA default) it is next year's sale.
        _doc, settle, _ = _ca_edge({"margin": self.DEC31})
        self.assertEqual(self._rows(settle),
                         [("margin", 5.0, 250.0, "2025-12-31")])
        self.assertNotIn("(by ", settle["year_boundary"]
                         ["deferred_at_year_end"][0]["why"])

    def test_blended_accounts_split_on_trade_dates(self):
        # margin2 buys 5 in December; margin sells its 5 on Dec 31. On
        # trade dates the pool left is margin2's 5 with half the
        # deferral; on settlement dates both accounts hold 5.
        books = {"margin": self.DEC31,
                 "margin2": [_ct("margin2", "2025-12-01", "2025-12-02",
                                 CA_STOCK, 5, -500)]}
        _doc, trade, _ = _ca_edge(books, tax_date="trade")
        self.assertEqual(self._rows(trade),
                         [("margin2", 5.0, 125.0, "2025-12-31")])
        _doc, settle, _ = _ca_edge(books)
        self.assertEqual(sorted(self._rows(settle)),
                         [("margin", 5.0, 125.0, "2025-12-31"),
                          ("margin2", 5.0, 125.0, "2025-12-31")])


if __name__ == "__main__":
    unittest.main()

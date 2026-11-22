"""2026-09 US-engine audit pins: §1223(3) per-share tacking and the
option-leg-first ASSIGN tie-break.

Formerly: test_us_engine_audit"""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def _T(**kw):
    d = dict(action="BUYSELL", time="09:30:00", symbol="AAPL.US",
             currency="USD", account="m")
    d.update(kw)
    return TaxTransaction(**d)


def _run(book):
    with redirect_stderr(io.StringIO()):
        return get_tax_rules("usa").compute_gains(book)


class TestAssignLegOrdering(unittest.TestCase):
    """Formerly: test_us_engine_audit.TestAssignLegOrdering"""
    def _book(self, stock_first):
        opt = "AAPL250620C00100000.US"
        legs = [
            TaxTransaction(action="ASSIGN", date="2025-06-20",
                           time="09:30:00", symbol="AAPL.US",
                           quantity=-100, currency="USD",
                           net_amount=10000.0, account="m"),
            TaxTransaction(action="ASSIGN", date="2025-06-20",
                           time="09:30:00", symbol=opt, quantity=1,
                           currency="USD", net_amount=0.0, account="m"),
        ]
        if not stock_first:
            legs.reverse()
        return [
            _T(date="2025-01-05", quantity=100, net_amount=9000.0),
            TaxTransaction(action="BUYSELL", date="2025-05-01",
                           time="09:30:00", symbol=opt, quantity=-1,
                           currency="USD", net_amount=300.0,
                           account="m"),
        ] + legs

    @rule(R.US_OPT_02)
    def test_stock_leg_listed_first_still_gets_premium(self):
        """Same-timestamp assignment legs listed stock-first used to
        drop the premium (an "unconsumed option-assignment adjustment"
        warning) — proceeds 10,000 instead of 10,300. The option leg
        now sorts first regardless of input order."""
        for stock_first in (False, True):
            r = _run(self._book(stock_first))
            stock_rows = [t for t in r["transactions"]
                          if t.get("symbol") == "AAPL.US"
                          and t.get("qty") and "gain" in t]
            self.assertEqual(len(stock_rows), 1, stock_rows)
            self.assertEqual(round(float(stock_rows[0]["gain"]), 2),
                             1300.0, (stock_first, stock_rows[0]))


if __name__ == "__main__":
    unittest.main()

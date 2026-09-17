"""2026-09 US-engine audit pins: §1223(3) per-share tacking and the
option-leg-first ASSIGN tie-break."""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.lib.core import TaxTransaction, get_tax_rules


def _T(**kw):
    d = dict(action="BUYSELL", time="09:30:00", symbol="AAPL.US",
             currency="USD", account="m")
    d.update(kw)
    return TaxTransaction(**d)


def _run(book):
    with redirect_stderr(io.StringIO()):
        return get_tax_rules("usa").compute_gains(book)


class TestPerShareTacking(unittest.TestCase):
    def test_unmatched_remainder_keeps_its_own_term(self):
        """Buy 100 (2024-06-01); sell 100 at a loss 2025-05-20 (353
        days); buy 200 on 2025-05-25 (100 are the replacement); sell
        200 on 2025-06-10. §1223(3) tacks the 353 days onto the 100
        replacement shares only: those are LONG_TERM (353+16), the
        other 100 were held 16 days -> SHORT_TERM. Whole-lot tacking
        reported one 200-share LONG_TERM row."""
        book = [
            _T(date="2024-06-01", quantity=100, net_amount=10000.0),
            _T(date="2025-05-20", quantity=-100, net_amount=8000.0),
            _T(date="2025-05-25", quantity=200, net_amount=16000.0),
            # Sold at a GAIN so no second wash can match the unmatched
            # 100 (a second loss legitimately tacks them too — a
            # different question).
            _T(date="2025-06-10", quantity=-200, net_amount=21000.0),
        ]
        r = _run(book)
        rows = [t for t in r["transactions"]
                if t.get("qty") and "gain" in t
                and t.get("date") == "2025-06-10"]
        terms = sorted((float(t["qty"]), t["term"]) for t in rows)
        self.assertEqual(terms, [(100.0, "LONG_TERM"),
                                 (100.0, "SHORT_TERM")], rows)
        # Money conserved: the deferred 2,000 lands on the tacked 100
        # (cost 10,000 -> gain 500 LT) and the remainder is plain
        # (cost 8,000 -> gain 2,500 ST).
        by_term = {t["term"]: round(float(t["gain"]), 2) for t in rows}
        self.assertEqual(by_term, {"LONG_TERM": 500.0,
                                   "SHORT_TERM": 2500.0})
        # Wash + no-wash conservation on the fully-liquidated book.
        with redirect_stderr(io.StringIO()):
            n = get_tax_rules("usa").compute_gains(
                book, detect_wash_sales=False)
        allw = round(sum(float(t["gain"]) for t in r["transactions"]
                         if t.get("qty") and "gain" in t), 2)
        alln = round(sum(float(t["gain"]) for t in n["transactions"]
                         if t.get("qty") and "gain" in t), 2)
        self.assertEqual(allw, alln)


class TestAssignLegOrdering(unittest.TestCase):
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

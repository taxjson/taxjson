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


@rule(R.US_WASH_10)
class TestPerShareTacking(unittest.TestCase):
    """Formerly: test_us_engine_audit.TestPerShareTacking"""
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


if __name__ == "__main__":
    unittest.main()

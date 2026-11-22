"""Canada engine fixes from the 2026-09 engine audit (repros r04, r08,
r08b, r11 of that audit), each pinned by a test that failed before.

Formerly: test_ca_engine_audit_fixes"""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R


def T(action="BUYSELL", date="", symbol="", quantity=0.0, net_amount=0.0,
      currency="USD", time="10:00:00", settle="", account="55500001", **kw):  # pii-ok (synthetic id)
    return TaxTransaction(action=action, date=date, symbol=symbol,
                          quantity=quantity, net_amount=net_amount,
                          currency=currency, time=time,
                          date_settle=settle or date, account=account, **kw)


def ca(book, **kw):
    err = io.StringIO()
    with redirect_stderr(err):
        r = get_tax_rules("canada").compute_gains(book, **kw)
    r["_stderr"] = err.getvalue()
    return r


def records(r):
    return [g for g in r["transactions"] if "gain" in g]


class TestRocOnEmptyPool(unittest.TestCase):
    """A return of capital that posts after the position was fully sold
    has no ACB to reduce: a capital gain in the year received (s.40(3)
    with a nil ACB), never a reduction of the NEXT purchase's ACB.

    Formerly: test_ca_engine_audit_fixes.TestRocOnEmptyPool"""

    def _book(self):
        s = "XRE.TO"
        return [T(date="2025-01-06", symbol=s, quantity=100, net_amount=1000.0, currency="CAD"),
                T(date="2025-02-05", symbol=s, quantity=-100, net_amount=1100.0, currency="CAD"),
                T(action="ADJUST", date="2025-03-01", symbol=s, quantity=0.0, net_amount=-60.0,
                  currency="CAD", type="roc"),
                T(date="2026-04-01", symbol=s, quantity=50, net_amount=500.0, currency="CAD"),
                T(date="2026-06-01", symbol=s, quantity=-50, net_amount=500.0, currency="CAD")]

    @rule(R.CA_ACB_07)
    def test_post_drain_roc_is_a_gain_in_its_year_and_does_not_leak(self):
        r = ca(self._book())
        recs = [(g["date"], round(g["gain"], 2)) for g in records(r)]
        # Before: the -60 lowered the 2026 purchase's ACB to 440, so the
        # 2026 sale showed +60 and 2025 nothing for the ROC.
        self.assertEqual(recs, [("2025-02-05", 100.0), ("2025-03-01", 60.0), ("2026-06-01", 0.0)])
        roc = [g for g in records(r) if g["date"] == "2025-03-01"][0]
        self.assertEqual(roc["qty"], 0.0)
        self.assertIn("s.40(3)", roc["note"])
        self.assertIn("EMPTY pool", r["_stderr"])

    def test_positive_adjust_on_empty_pool_keeps_the_old_path(self):
        # Not a return of capital: unchanged (warned, carried forward).
        book = self._book()
        book[2] = T(action="ADJUST", date="2025-03-01", symbol="XRE.TO", quantity=0.0,
                    net_amount=60.0, currency="CAD")
        r = ca(book)
        self.assertEqual([round(g["gain"], 2) for g in records(r)], [100.0, -60.0])


if __name__ == "__main__":
    unittest.main()

"""Medium-round fixes to `tjs option-boundary` (lib/option_boundary.py):
R1-36 / R1-174 / R1-190 (future expiry is open, not 'expired?'), R1-179
(rename-SPLIT followed, signed premium), S075-09 (cash-settled index
option assignment), S075-10 (lock recorded close timing, project on
grant timing).

Formerly: test_fix_filing_a_ob"""
import unittest
from datetime import date

from taxjson.model.transaction import TaxTransaction
from taxjson.lib.option_boundary import straddling
from tax_rules import rule
from taxjson.rules import ids as R


def T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


TODAY = date(2026, 9, 29)


@rule(R.CA_OPT_07)
class TestFutureExpiryIsOpen(unittest.TestCase):
    """R1-36 / R1-174 / R1-190: a contract whose expiry is after today is
    simply open; only an expiry already passed with no close row is
    ATTENTION.

    Formerly: test_fix_filing_a_ob.TestFutureExpiryIsOpen"""

    def test_expiry_after_today_is_open(self):
        book = [T(date="2026-09-16", date_settle="2026-09-17",
                  symbol="XYZ261120C00080000.TO", quantity=-3, price=1.1,
                  net_amount=329.25)]
        rows = straddling(book, 2026, "grant", 2025, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_kind"], "open")
        self.assertFalse(rows[0]["attention"])
        self.assertIn("premium 329.25 recognised in 2026; open",
                      rows[0]["where"])

    def test_expiry_passed_without_row_is_attention(self):
        book = [T(date="2026-06-16", date_settle="2026-06-17",
                  symbol="ABC260821C00012000.TO", quantity=-1, price=2,
                  net_amount=199.0)]
        rows = straddling(book, 2026, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "expired?")
        self.assertTrue(rows[0]["attention"])

    def test_closed_year_uses_year_end(self):
        # A 2025 project run in 2026: every 2025 expiry is in the past.
        book = [T(date="2025-03-03", date_settle="2025-03-04",
                  symbol="ZZZ250620P00040000.US", quantity=-2, price=1.5,
                  net_amount=300.0)]
        rows = straddling(book, 2025, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "expired?")


@rule(R.CA_OPT_07)
class TestRenameAndSign(unittest.TestCase):
    """R1-179.

    Formerly: test_fix_filing_a_ob.TestRenameAndSign"""

    def test_rename_split_is_followed(self):
        old, new = "XYZ260320C00050000.TO", "XYZ1260320C00050000.TO"
        book = [T(date="2025-12-01", date_settle="2025-12-02", symbol=old,
                  quantity=-2, price=3, net_amount=600.0),
                TaxTransaction(action="SPLIT", date="2025-12-15",
                               date_settle="2025-12-15", symbol=old,
                               symbol_new=new, quantity=1.0,
                               currency="CAD", account="margin"),
                T(date="2026-02-02", date_settle="2026-02-03", symbol=new,
                  quantity=2, price=0.5, net_amount=100.0)]
        rows = straddling(book, 2026, "grant", 2025, today=TODAY)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["close_kind"], "buy-back")
        self.assertEqual(r["paid"], 100.0)
        self.assertEqual(r["premium"], 600.0)
        self.assertFalse(r["attention"])

    def test_negative_net_write_keeps_its_sign(self):
        book = [T(date="2025-12-01", date_settle="2025-12-02",
                  symbol="ABC260116C00090000.US", quantity=-1, price=0.01,
                  net_amount=-2.35, currency="USD")]
        rows = straddling(book, 2025, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["premium"], -2.35)
        self.assertIn("-2.35", rows[0]["where"])


if __name__ == "__main__":
    unittest.main()

"""Medium-round fixes to `taxjson option-boundary` (lib/option_boundary.py):
R1-36 / R1-174 / R1-190 (future expiry is open, not 'expired?'), R1-179
(rename-SPLIT followed, signed premium), S075-09 (cash-settled index
option assignment), S075-10 (lock recorded close timing, project on
grant timing)."""
import unittest
from datetime import date

from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_boundary import straddling


def T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


TODAY = date(2026, 9, 29)


class TestFutureExpiryIsOpen(unittest.TestCase):
    """R1-36 / R1-174 / R1-190: a contract whose expiry is after today is
    simply open; only an expiry already passed with no close row is
    ATTENTION."""

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


class TestRenameAndSign(unittest.TestCase):
    """R1-179."""

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


class TestCashSettledAssignment(unittest.TestCase):
    """S075-09: an index option (underlying never trades as stock in the
    book) settles in cash — no s.49(3) fold, no T1-ADJ."""

    BOOK = [T(date="2025-12-10", date_settle="2025-12-11",
              symbol="XSP260116P00500000.US", quantity=-1, price=5,
              net_amount=500.0, currency="USD"),
            TaxTransaction(action="ASSIGN", date="2026-01-16",
                           date_settle="2026-01-16",
                           symbol="XSP260116P00500000.US", quantity=1,
                           price=3, net_amount=300.0, currency="USD",
                           account="margin")]

    def test_no_fold_no_amendment(self):
        rows = straddling(self.BOOK, 2026, "grant", 2025,
                          filed_years={2025}, today=TODAY)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["close_kind"], "cash-settled")
        self.assertEqual(r["paid"], 300.0)
        self.assertFalse(r["action"].startswith("T1-ADJ"))
        self.assertNotIn("remove", r["action"])
        self.assertIn("500.00", r["where"])
        self.assertIn("2025", r["where"])

    def test_stock_leg_present_is_a_real_assignment(self):
        book = self.BOOK + [TaxTransaction(
            action="ASSIGN", date="2026-01-16", date_settle="2026-01-19",
            symbol="XSP.US", quantity=100, price=500, net_amount=50000.0,
            currency="USD", account="margin")]
        rows = straddling(book, 2026, "grant", 2025, filed_years={2025},
                          today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "assignment")
        self.assertTrue(rows[0]["action"].startswith("T1-ADJ 2025"))


class TestLockRecordsCloseTiming(unittest.TestCase):
    """S075-10: the 2025 lock records CLOSE timing (the 2025 return did not
    report the premium) while this project puts 2025 writes on grant
    timing (since = 2025)."""

    OPT = "Q260116C00050000.TO"
    LOCK = {2025: {"option_premium_timing": "close",
                   "option_grant_since": None}}

    def _book(self):
        o = self.OPT
        return [T(date="2025-12-15", date_settle="2025-12-16", symbol=o,
                  quantity=-3, price=4, net_amount=1197.0),
                T(date="2026-01-10", date_settle="2026-01-12", symbol=o,
                  quantity=1, price=1, net_amount=101.0),
                T(date="2026-01-16", date_settle="2026-01-16", symbol=o,
                  quantity=1, price=0, net_amount=0.0),
                TaxTransaction(action="ASSIGN", date="2026-01-16",
                               date_settle="2026-01-16", symbol=o,
                               quantity=1, price=0, net_amount=0.0,
                               currency="CAD", account="margin"),
                TaxTransaction(action="ASSIGN", date="2026-01-16",
                               date_settle="2026-01-19", symbol="Q.TO",
                               quantity=-100, price=50, net_amount=5000.0,
                               currency="CAD", account="margin")]

    def test_rows_flag_the_premium_no_return_reports(self):
        rows = straddling(self._book(), 2026, "grant", 2025,
                          filed_timing=self.LOCK, today=TODAY)
        by = {r["close_kind"]: r for r in rows}
        for kind in ("buy-back", "expiry"):
            self.assertTrue(by[kind]["attention"], kind)
            self.assertIn("close timing", by[kind]["action"])
            self.assertIn("option_grant_timing_since = 2026",
                          by[kind]["action"])
            self.assertIn("T1-ADJ 2025", by[kind]["action"])
            self.assertNotIn("no amendment", by[kind]["action"])
        a = by["assignment"]
        self.assertFalse(a["attention"])
        self.assertNotIn("remove", a["action"])
        self.assertNotIn("was filed with", a["action"])
        self.assertIn("no amendment", a["action"])

    def test_still_open_is_flagged(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16",
                  symbol="Q270115C00050000.TO", quantity=-1, price=4,
                  net_amount=399.0)]
        rows = straddling(book, 2026, "grant", 2025,
                          filed_timing=self.LOCK, today=TODAY)
        self.assertTrue(rows[0]["attention"])
        self.assertIn("option_grant_timing_since = 2026", rows[0]["action"])

    def test_lock_on_grant_is_unchanged(self):
        rows = straddling(self._book(), 2026, "grant", 2025,
                          filed_timing={2025: {"option_premium_timing":
                                               "grant",
                                               "option_grant_since": 2025}},
                          today=TODAY)
        by = {r["close_kind"]: r for r in rows}
        self.assertTrue(by["expiry"]["action"].startswith("no amendment"))
        self.assertTrue(by["assignment"]["action"].startswith("T1-ADJ 2025"))


if __name__ == "__main__":
    unittest.main()

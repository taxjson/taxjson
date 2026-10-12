"""Low-round fixes to `taxjson option-boundary` (lib/option_boundary.py):
R1-39 / R1-180 (statutory citations), S075-00 (an expiry after Dec 31 but
inside the books), S075-02 / S075-03 / S075-04 (pins), S075-05 (the
project's tax_date dates writes and closes)."""
import unittest
from datetime import date

from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_boundary import straddling
from tax_rules import rule


def T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


TODAY = date(2026, 9, 29)


def _assigned(opt, under, stock_qty):
    return [T(date="2025-12-15", date_settle="2025-12-16", symbol=opt,
              quantity=-1, price=4, net_amount=399.0),
            T(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16",
              symbol=opt, quantity=1, price=0, net_amount=0.0),
            T(action="ASSIGN", date="2026-01-16", date_settle="2026-01-19",
              symbol=under, quantity=stock_qty, price=50,
              net_amount=5000.0)]


class TestCitations(unittest.TestCase):
    """R1-39 / R1-180: s.49(3) for a call, s.49(3.1) for a put (never
    s.49(2), the corporate-issuer expiry rule); IT-479R para 29 / 32 for
    the buy-back loss (para 24 is about financial institutions)."""

    @rule("CA-OPT-06")
    def test_put_assignment_cites_49_3_1(self):
        rows = straddling(_assigned("Q260116P00050000.TO", "Q.TO", 100),
                          2025, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "assignment")
        self.assertIn("(s.49(3.1))", rows[0]["where"])
        self.assertNotIn("49(2)", rows[0]["where"] + rows[0]["action"])

    @rule("CA-OPT-06")
    def test_call_assignment_cites_49_3(self):
        rows = straddling(_assigned("Q260116C00050000.TO", "Q.TO", -100),
                          2025, "grant", 2025, today=TODAY)
        self.assertIn("(s.49(3))", rows[0]["where"])
        self.assertNotIn("49(2)", rows[0]["where"])

    @rule("CA-OPT-03", "CA-OPT-07")
    def test_buyback_cites_it479r_29_or_32(self):
        for right, para in (("C", "para 29"), ("P", "para 32")):
            opt = f"Q260116{right}00050000.TO"
            book = [T(date="2025-12-15", date_settle="2025-12-16",
                      symbol=opt, quantity=-1, price=4, net_amount=399.0),
                    T(date="2026-01-05", date_settle="2026-01-06",
                      symbol=opt, quantity=1, price=0.5, net_amount=50.0)]
            rows = straddling(book, 2025, "grant", 2025, today=TODAY)
            self.assertEqual(rows[0]["action"],
                             f"no amendment (IT-479R {para})")


@rule("CA-OPT-07")
class TestExpiryInsideTheBooks(unittest.TestCase):
    """S075-00: books running into January of the next year cover a
    January expiry — a write still open after it is missing its row."""

    def test_january_expiry_before_last_data_date_is_attention(self):
        book = [T(date="2025-12-01", date_settle="2025-12-02",
                  symbol="XYZ260116C00050000.TO", quantity=-2, price=1.5,
                  net_amount=300.0),
                T(date="2026-01-30", date_settle="2026-02-03",
                  symbol="ABC.TO", quantity=10, price=10, net_amount=100.0)]
        rows = straddling(book, 2025, "grant", 2025, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_kind"], "expired?")
        self.assertTrue(rows[0]["attention"])

    def test_january_expiry_after_the_books_stays_open(self):
        # Books stop at Dec 31: nothing says the January expiry is
        # missing (the next year's export is not in yet).
        book = [T(date="2025-12-01", date_settle="2025-12-02",
                  symbol="XYZ260116C00050000.TO", quantity=-2, price=1.5,
                  net_amount=300.0)]
        rows = straddling(book, 2025, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "open")
        self.assertFalse(rows[0]["attention"])

    def test_expiry_after_today_stays_open_even_inside_the_books(self):
        book = [T(date="2026-09-01", date_settle="2026-09-02",
                  symbol="XYZ261016C00050000.TO", quantity=-1, price=1.5,
                  net_amount=150.0),
                T(date="2026-12-01", date_settle="2026-12-02",
                  symbol="ABC.TO", quantity=10, price=10, net_amount=100.0)]
        rows = straddling(book, 2026, "grant", 2025, today=TODAY)
        self.assertEqual(rows[0]["close_kind"], "open")


class TestPins(unittest.TestCase):

    @rule("CA-OPT-06", "CA-OPT-07")
    def test_unfiled_assignment_names_the_premium(self):
        # S075-02: the unfiled-year branch prints the premium amount.
        rows = straddling(_assigned("Q260116C00050000.TO", "Q.TO", -100),
                          2025, "grant", 2025, today=TODAY)
        self.assertEqual(
            rows[0]["action"],
            "if 2025 was filed with the 399.00 premium as a gain, amend "
            "2025 to remove it (s.49(4)); otherwise nothing")

    @rule("CA-OPT-04", "CA-OPT-07")
    def test_dec31_expiry_with_no_row_is_expired(self):
        # S075-03: an OCC expiry of exactly Dec 31 is <= year end.
        book = [T(date="2025-06-02", date_settle="2025-06-03",
                  symbol="XYZ251231P00040000.TO", quantity=-1, price=2,
                  net_amount=200.0)]
        rows = straddling(book, 2025, "grant", 2025, today=TODAY)
        self.assertEqual((rows[0]["close_kind"], rows[0]["attention"]),
                         ("expired?", True))

    DEC31 = [T(date="2025-12-31", date_settle="2026-01-02",
               symbol="XYZ260320C00050000.TO", quantity=-1, price=3,
               net_amount=300.0),
             T(date="2026-02-10", date_settle="2026-02-11",
               symbol="XYZ260320C00050000.TO", quantity=1, price=1,
               net_amount=100.0)]

    @rule("CA-OPT-01", "CA-DATE-01")
    def test_settle_basis_dates_the_write_by_settlement(self):
        # S075-04: settles 2026-01-02 -> a 2026 write closed in 2026.
        self.assertEqual(straddling(self.DEC31, 2025, "close", None,
                                    today=TODAY), [])
        self.assertEqual(straddling(self.DEC31, 2025, "close", None,
                                    today=TODAY, tax_date="settle"), [])

    @rule("CA-OPT-01", "CA-DATE-02")
    def test_trade_basis_dates_the_write_by_trade_date(self):
        # S075-05: on tax_date = "trade" the engine puts the write in
        # 2025 and the buy-back in 2026 — a straddle.
        rows = straddling(self.DEC31, 2025, "grant", 2025, today=TODAY,
                          tax_date="trade")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["write_year"], r["close_year"], r["close_kind"]),
                         (2025, 2026, "buy-back"))
        self.assertEqual(r["premium"], 300.0)
        self.assertEqual(r["paid"], 100.0)


if __name__ == "__main__":
    unittest.main()

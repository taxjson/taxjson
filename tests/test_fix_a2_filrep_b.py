"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import unittest
from datetime import date

from taxjson.lib.core import TaxTransaction
from tax_rules import rule

GRANT25 = {2025: {"option_premium_timing": "grant", "option_grant_since": 2025}}


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base.get("date"))
    return TaxTransaction(**base)


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestOptionBoundaryClassRoot(unittest.TestCase):
    """A2-0114 (regression of S075-09), A2-0328: an assignment whose
    option root drops the share class (RCI for RCI.B.TO, BRKB for
    BRK.B.US) is physically settled — option-boundary resolves the root
    with the engine's resolver instead of calling it cash-settled."""

    def _book(self, stock):
        return [
            T(date="2025-12-01", date_settle="2025-12-02",
              symbol="RCI260116P00050000.TO", quantity=-1, price=2,
              net_amount=199.0),
            T("ASSIGN", date="2026-01-16", symbol="RCI260116P00050000.TO",
              quantity=1, price=0, net_amount=0.0, time="16:00:00"),
            T(date="2026-01-16", date_settle="2026-01-19", symbol=stock,
              quantity=100, price=50, net_amount=5000.0, time="16:00:00"),
            T(date="2026-03-02", date_settle="2026-03-03", symbol=stock,
              quantity=-100, price=52, net_amount=5200.0)]

    @rule("CA-OPT-07")
    def test_a2_0114_class_share_put_is_an_assignment(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.B.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])
        self.assertIn("T1-ADJ 2025: remove the 199.00 premium",
                      rows[0]["action"])

    @rule("CA-OPT-07")
    def test_a2_0114_exact_root_control_unchanged(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])

    @rule("CA-OPT-07")
    def test_a2_0328_brkb_call_root_resolves_to_class_line(self):
        from taxjson.lib.option_boundary import write_lots
        book = [
            T(date="2024-12-02", symbol="BRKB250117C00450000.US",
              quantity=-1, price=3, net_amount=300.0, currency="USD"),
            T("ASSIGN", date="2025-01-17", symbol="BRKB250117C00450000.US",
              quantity=1, price=0, net_amount=0.0, currency="USD"),
            T("ASSIGN", date="2025-01-17", date_settle="2025-01-21",
              symbol="BRK.B.US", quantity=-100, price=450,
              net_amount=45000.0, currency="USD")]
        lots, _ = _quiet(write_lots, book)
        self.assertEqual([c.kind for c in lots[0].closes], ["assignment"])

    @rule("CA-OPT-07")
    def test_a2_0328_index_option_still_cash_settled(self):
        from taxjson.lib.option_boundary import write_lots
        book = [
            T(date="2025-12-10", symbol="XSP260116P00500000.US",
              quantity=-1, price=5, net_amount=500.0, currency="USD"),
            T("ASSIGN", date="2026-01-16", symbol="XSP260116P00500000.US",
              quantity=1, price=3, net_amount=300.0, currency="USD")]
        lots, _ = _quiet(write_lots, book)
        self.assertEqual([c.kind for c in lots[0].closes], ["cash-settled"])


class TestOptionBoundaryBuybackSign(unittest.TestCase):
    """A2-1111: a .tt book carries a buy-back's net_amount negative
    (money out); the cost of the cover is its magnitude."""

    @rule("CA-OPT-07")
    def test_a2_1111_negative_buyback_net_is_a_cost(self):
        from taxjson.lib.option_boundary import straddling
        S = "Q260116C00050000.TO"
        for net in (-101.0, 101.0):
            book = [T(date="2025-12-15", symbol=S, quantity=-1, price=4,
                      net_amount=399.0),
                    T(date="2026-01-10", symbol=S, quantity=1, price=1,
                      net_amount=net)]
            rows, _ = _quiet(straddling, book, 2026, "close", None)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["paid"], 101.0)
            self.assertIn("net 298.00", r["where"])
            self.assertIn("-101.00 in 2026", r["action"])
            self.assertNotIn("--", r["action"])


class TestOptionBoundaryExpiryDay(unittest.TestCase):
    """A2-1112: on its own expiry day a contract is still open (the
    broker posts the expiry row afterwards), as the run's own
    expired-options warning already treats it."""

    S = "Q261001C00050000.TO"

    def _book(self):
        return [T(date="2026-09-01", symbol=self.S, quantity=-1, price=4,
                  net_amount=399.0),
                T(date="2026-09-02", symbol="Z261001C00010000.TO",
                  quantity=1, price=1, net_amount=101.0)]

    @rule("CA-OPT-07")
    def test_a2_1112_expiry_day_is_open(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 1)
        self.assertEqual(expired_open(self._book(), 2026, today=today), [])
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["open"])

    @rule("CA-OPT-07")
    def test_a2_1112_day_after_expiry_is_missing_row(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 2)
        self.assertEqual(len(expired_open(self._book(), 2026,
                                          today=today)), 2)
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["expired?"])

    @rule("CA-OPT-07")
    def test_a2_1112_dec31_expiry_of_a_past_year_still_flagged(self):
        from taxjson.lib.option_boundary import expired_open
        book = [T(date="2025-09-01", symbol="Q251231C00050000.TO",
                  quantity=-1, price=4, net_amount=399.0)]
        rows = expired_open(book, 2025, today=date(2026, 3, 1))
        self.assertEqual([r["symbol"] for r in rows],
                         ["Q251231C00050000.TO"])


if __name__ == "__main__":
    unittest.main()

"""A return of capital on a Canadian holding of fewer units than the share
tolerance that still carries its cost (backlog TJ-5001, TJ-5140).

Every fixture is synthetic: an invented ticker, account id and amounts."""
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


class TestRocOnAHoldingUnderTheUnitTolerance(unittest.TestCase):
    """TJ-5001: one share bought for 100 and consolidated to 0.0000005 is
    still a holding (its cost is material, as `AcbPool.drain` reads it):
    a return of capital reduces its ACB. Read by the unit count alone it
    was an empty pool: a gain of 20 in 2025, the cost kept at 100 and a
    loss of 20 on the 2026 sale, with a warning that the position was
    sold."""

    S = "SYN.TO"

    def _book(self, roc=-20.0, ratio=0.0000005, **kw):
        return [T(date="2024-03-01", symbol=self.S, quantity=1, net_amount=100.0, currency="CAD"),
                T(action="SPLIT", date="2024-09-03", symbol=self.S, quantity=ratio, currency="CAD"),
                T(action="ADJUST", date="2025-06-02", symbol=self.S, quantity=0.0, net_amount=roc,
                  currency="CAD", **kw),
                T(date="2026-03-02", symbol=self.S, quantity=-ratio, net_amount=80.0, currency="CAD")]

    @rule(R.CA_ACB_07)
    def test_the_return_of_capital_reduces_the_acb(self):
        for ratio in (0.0000005, 0.001):
            with self.subTest(ratio=ratio):
                r = ca(self._book(ratio=ratio, type="roc"))
                self.assertEqual([(g["date"], round(g["gain"], 2)) for g in records(r)],
                                 [("2026-03-02", 0.0)])
                self.assertNotIn("EMPTY pool", r["_stderr"])
                self.assertEqual(r["inventory"], [])

    @rule(R.CA_ACB_07)
    def test_one_that_would_take_all_the_cost_is_a_gain_and_the_cost_stays(self):
        """Units under the tolerance with no cost are no position to the
        pool or to the sale that closes them (it would open a short, the
        gain lost; backlog TJ-5140): a reduction that would leave none is
        booked as before the fix, the amount a gain in its year and the
        cost kept for the sale. The totals are those of a holding of any
        size; the warning is the case's own, once."""
        for name, rocs, total in (("all of it", (-100.0,), 80.0),
                                  ("more than it", (-120.0,), 100.0),
                                  ("all but a fifth of a cent", (-99.998,), 79.998),
                                  ("in two parts", (-60.0, -60.0), 100.0)):
            with self.subTest(name):
                book = self._book(roc=rocs[0], type="roc")
                for more in rocs[1:]:
                    book.insert(3, T(action="ADJUST", date="2025-07-02", symbol=self.S,
                                     quantity=0.0, net_amount=more, currency="CAD", type="roc"))
                r = ca(book)
                self.assertAlmostEqual(sum(g["gain"] for g in records(r)), total, places=4)
                self.assertEqual(r["inventory"], [])
                self.assertEqual(r["_stderr"].count("would leave no cost on a holding of 5e-07 unit(s)"), 1)
                self.assertNotIn("EMPTY pool", r["_stderr"])
                self.assertNotIn("fully sold", r["_stderr"])
                kept = [g for g in records(r) if g["qty"] == 0.0][-1]
                self.assertIn("its cost kept", kept["note"])
                self.assertEqual(round(records(r)[-1]["cost"], 2), 100.0 if len(rocs) == 1 else 40.0)

    def test_the_guard_follows_what_the_sale_reads(self):
        # A reversal after it, cost added before it, and a holding of
        # exactly the tolerance (left in the pool by its drain, an opening
        # to the sale once it has no cost): the totals of a larger holding.
        roc = dict(action="ADJUST", symbol=self.S, quantity=0.0, currency="CAD", type="roc")
        book = self._book(roc=-100.0, type="roc")
        book.insert(3, T(date="2025-07-02", net_amount=100.0, **roc))
        self.assertAlmostEqual(sum(g["gain"] for g in records(ca(book))), -20.0, places=4)
        book = self._book(roc=15.0)
        book.insert(3, T(date="2025-07-02", net_amount=-115.0, **roc))
        r = ca(book)
        self.assertAlmostEqual(sum(g["gain"] for g in records(r)), 80.0, places=4)
        self.assertEqual(r["inventory"], [])
        for amount, total, said in ((-20.0, 0.0, False), (-100.0, 80.0, True)):
            with self.subTest(amount=amount):
                r = ca(self._book(roc=amount, ratio=0.000001, type="roc"))
                self.assertAlmostEqual(sum(g["gain"] for g in records(r)), total, places=4)
                self.assertEqual(r["inventory"], [])
                self.assertEqual("would leave no cost" in r["_stderr"], said)
                self.assertNotIn("stranded basis", r["_stderr"])
        # A holding the tolerance does not reach is reduced to nil as ever.
        r = ca(self._book(roc=-120.0, ratio=0.001, type="roc"))
        self.assertEqual([(g["date"], round(g["gain"], 2)) for g in records(r)],
                         [("2025-06-02", 20.0), ("2026-03-02", 80.0)])
        self.assertNotIn("would leave no cost", r["_stderr"])

    def test_a_cost_added_lands_on_the_holding_unwarned(self):
        r = ca(self._book(roc=15.0))
        self.assertEqual([(g["date"], round(g["gain"], 2)) for g in records(r)],
                         [("2026-03-02", -35.0)])
        self.assertNotIn("EMPTY pool", r["_stderr"])


if __name__ == "__main__":
    unittest.main()

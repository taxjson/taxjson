"""Canada engine fixes from the 2026-09 engine audit (repros r04, r08,
r08b, r11 of that audit), each pinned by a test that failed before."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.core import TaxTransaction, get_tax_rules


def T(action="BUYSELL", date="", symbol="", quantity=0.0, net_amount=0.0,
      currency="USD", time="10:00:00", settle="", account="55500001", **kw):
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


class TestNegativeProceedsSell(unittest.TestCase):
    """A sell whose commission exceeds its gross has NEGATIVE proceeds."""

    def test_questrade_worthless_option_close_books_negative_proceeds(self):
        # Audit r11: closing a worthless long call at $0.01 (gross 1.00,
        # commission 10.95). Questrade's parser emits net = -9.95; the
        # engine booked abs() = +9.95 as proceeds (-201.00 instead of
        # -220.90).
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        csv = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,Gross Amount,Commission,Net Amount,Currency,Account #,Activity Type,Account Type\n"
               "2025-03-03 10:00:00 AM,2025-03-04 12:00:00 AM,Buy,,CALL ZZZ 06/20/25 50.00 ZZZ INC,1,2.00,-200.00,-10.95,-210.95,USD,55500001,Trades,Individual\n"
               "2025-12-15 10:00:00 AM,2025-12-16 12:00:00 AM,Sell,,CALL ZZZ 06/20/25 50.00 ZZZ INC,-1,0.01,1.00,-10.95,-9.95,USD,55500001,Trades,Individual\n")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "qt.csv"
            p.write_text(csv)
            with redirect_stderr(io.StringIO()):
                rows = QuestradeBrokerage().parse_file(p)
        txs = [TaxTransaction(**{k: v for k, v in row.items()
                                 if k in TaxTransaction.__dataclass_fields__})
               for row in rows]
        (g,) = records(ca(txs))
        self.assertAlmostEqual(g["proceeds"], -9.95, places=2)
        self.assertAlmostEqual(g["gain"], -220.90, places=2)

    def test_write_for_less_than_the_commission_is_a_loss_at_grant(self):
        # Premium 5.00, commission 9.95: net -4.95 — the grant record is
        # the small loss the design doc describes, not a +4.95 gain.
        opt = "ZZZ260116C00050000.US"
        book = [T(date="2025-06-02", symbol=opt, quantity=-1, net_amount=-4.95),
                T(date="2026-01-16", symbol=opt, quantity=1, net_amount=0.0)]
        grant = records(ca(book, option_premium_timing="grant"))
        self.assertEqual([round(g["gain"], 2) for g in grant], [-4.95])
        close = records(ca(book))
        self.assertEqual([round(g["gain"], 2) for g in close], [-4.95])

    def test_ordinary_sells_and_buys_are_unchanged(self):
        # A buy spelled with a negative amount (cash convention) is
        # still a cost; a normal sell's positive amount its proceeds.
        book = [T(date="2025-01-02", symbol="ZZZ.US", quantity=10, net_amount=-1000.0),
                T(date="2025-02-03", symbol="ZZZ.US", quantity=-10, net_amount=1200.0)]
        (g,) = records(ca(book))
        self.assertAlmostEqual(g["gain"], 200.0)


if __name__ == "__main__":
    unittest.main()

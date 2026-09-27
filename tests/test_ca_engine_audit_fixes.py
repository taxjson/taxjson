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


class TestPhaseLadderTriggerClassification(unittest.TestCase):
    """Pre/post-loss triggers and their ADJUST placement use the ca_main
    phase ladder the pool replays with (audit r08 / r08b)."""

    def _r08(self, trigger_time):
        s = "PHZ.TO"
        return [T(date="2025-03-03", symbol=s, quantity=100, net_amount=2000.0, currency="CAD", settle="2025-03-04"),
                # broker row, T+1: executed the day before the loss sale
                T(date="2025-03-10", symbol=s, quantity=50, net_amount=500.0, currency="CAD", time=trigger_time, settle="2025-03-11"),
                # same-day-settle row (a .tt line) — the loss
                T(date="2025-03-11", symbol=s, quantity=-120, net_amount=1200.0, currency="CAD", time="10:00:00", settle="2025-03-11"),
                T(date="2026-02-02", symbol=s, quantity=-30, net_amount=300.0, currency="CAD", settle="2026-02-03")]

    def test_trigger_settling_with_the_loss_is_pre_loss_whatever_its_clock_time(self):
        # The 03-10 buy is in the pool the 03-11 sale draws from
        # (ladder: settle-lagged rows are pre-existing). Its clock time
        # (15:00 vs the sale's 10:00) must not make it "post-loss": its
        # ADJUST then landed BEFORE the sale and inflated the loss
        # (raw -1000 / denied 250 instead of -800 / 200).
        want = [(-800.0, 200.0), (-400.0, 0.0)]
        for tm in ("15:00:00", "09:00:00"):
            r = ca(self._r08(tm))
            got = [(round(g["raw_gain"], 2), round(g["disallowed_amount"], 2)) for g in records(r)]
            self.assertEqual(got, want, tm)

    def test_short_cover_book_converges(self):
        # Audit r08b (fuzz seed 10, settle-lagged): never converged.
        def row(d, st, tm, acct, act, sym, q, net, new=""):
            return T(action=act, date=d, symbol=sym, quantity=q, net_amount=net,
                     time=tm, settle=st, account=acct, symbol_new=new)
        tax = [row("2025-01-02", "2025-01-04", "13:41:00", "55500001", "BUYSELL", "AAA.US", -25, 150),
               row("2025-03-03", "2025-03-03", "10:36:00", "55500002", "BUYSELL", "AAA.US", 10, 80),
               row("2025-05-20", "2025-05-20", "11:22:00", "55500001", "BUYSELL", "AAA.US", -25, 150),
               row("2025-05-24", "2025-05-25", "13:40:00", "55500002", "BUYSELL", "AAA.US", 25, 250),
               row("2025-05-25", "2025-05-25", "11:36:00", "55500001", "BUYSELL", "AAA.US", 50, 700),
               row("2025-05-27", "2025-05-27", "00:00:01", "55500001", "SPLIT", "AAA.US", 3.0, 0.0),
               row("2025-06-27", "2025-06-27", "00:00:01", "55500001", "SPLIT", "AAA.US", 1.0, 0.0, "ZZB.US"),
               row("2025-10-25", "2025-10-28", "15:00:00", "55500001", "BUYSELL", "ZZB.US", -105.0, 1050.0)]
        shel = [row("2025-02-16", "2025-02-18", "11:30:00", "RRSP9", "BUYSELL", "AAA.US", 10, 80),
                row("2025-04-17", "2025-04-18", "11:30:00", "RRSP9", "BUYSELL", "AAA.US", 20, 200),
                row("2025-04-24", "2025-04-24", "11:30:00", "RRSP9", "BUYSELL", "AAA.US", -10, 100),
                row("2025-05-01", "2025-05-03", "11:30:00", "RRSP9", "BUYSELL", "AAA.US", 20, 240),
                row("2025-05-05", "2025-05-06", "11:30:00", "RRSP9", "BUYSELL", "AAA.US", 10, 100)]
        r = ca(tax, sheltered_transactions=shel)
        self.assertTrue(r["summary"]["wash_solver_converged"], r["_stderr"][-500:])
        self.assertNotIn("invariant broken", r["_stderr"])
        recs = records(r)
        # Cash conservation (the taxable pool drains): allowed gains
        # minus the permanently denied losses equal the net cash.
        cash = 150 - 80 + 150 - 250 - 700 + 1050
        self.assertAlmostEqual(sum(g["gain"] for g in recs)
                               - sum(g["permanently_disallowed"] for g in recs), cash, places=4)
        self.assertLess(max(abs(g["raw_gain"]) for g in recs), 1000.0)


if __name__ == "__main__":
    unittest.main()

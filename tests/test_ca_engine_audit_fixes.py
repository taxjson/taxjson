"""Canada engine fixes from the 2026-09 engine audit (repros r04, r08,
r08b, r11 of that audit), each pinned by a test that failed before."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.core import TaxTransaction, get_tax_rules


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


class TestSuperficialLossSubstitutedProperty(unittest.TestCase):
    """ITA s.54 "superficial loss": the taxpayer or an affiliated person
    ACQUIRED identical property in the 61-day window AND at its end owns
    the SUBSTITUTED property — the property acquired in the window. A
    registered account's units held BEFORE the window neither create
    nor back a denial (audit r04; real-data AMD / ENPH / XTD shapes)."""
    S = "QQQX.US"

    def _sell_at_loss(self, *extra):
        return [T(date="2025-01-02", symbol=self.S, quantity=100, net_amount=2000.0),
                T(date="2025-06-02", symbol=self.S, quantity=-100, net_amount=1000.0),   # loss 1,000
                *extra]

    def _loss(self, r):
        return [g for g in records(r) if g["date"] == "2025-06-02"][0]

    def test_rebuy_sold_in_window_with_old_rrsp_holding_is_allowed(self):
        # Audit r04: before, 1,000 PERMANENTLY denied on the strength of
        # the RRSP's 2020 shares.
        tax = self._sell_at_loss(
            T(date="2025-06-05", symbol=self.S, quantity=100, net_amount=1000.0),
            T(date="2025-06-20", symbol=self.S, quantity=-100, net_amount=1000.0))
        shel = [T(date="2020-01-02", symbol=self.S, quantity=100, net_amount=500.0, account="RRSP9")]
        g = self._loss(ca(tax, sheltered_transactions=shel))
        self.assertEqual((g["gain"], g["disallowed_amount"], g["permanently_disallowed"]), (-1000.0, 0.0, 0.0))

    def test_amd_shape_taxable_rebuy_held_defers_not_permanent(self):
        # Only the margin account bought in the window (40, of which it
        # sells 30 again before +30); the RRSP's 800 predate the window:
        # the 10 still held are denied and DEFERRED into the taxable
        # ACB, nothing permanent. (Before: 40 denied, 30 of them
        # permanently on the strength of the RRSP's old shares.)
        tax = self._sell_at_loss(
            T(date="2025-06-10", symbol=self.S, quantity=40, net_amount=400.0),
            T(date="2025-06-20", symbol=self.S, quantity=-30, net_amount=300.0),
            T(date="2025-09-02", symbol=self.S, quantity=-10, net_amount=100.0))
        shel = [T(date="2021-03-01", symbol=self.S, quantity=800, net_amount=9000.0, account="RRSP9")]
        r = ca(tax, sheltered_transactions=shel)
        g = self._loss(r)
        self.assertAlmostEqual(g["disallowed_amount"], 100.0)
        self.assertAlmostEqual(g["permanently_disallowed"], 0.0)
        # Every denied dollar comes back through the taxable pool: the
        # book's allowed total equals its net cash, nothing permanent.
        recs = records(r)
        self.assertEqual(sum(x["permanently_disallowed"] for x in recs), 0.0)
        self.assertAlmostEqual(sum(x["gain"] for x in recs), -1000.0)

    def test_enph_shape_registered_account_only_sold_in_window_is_allowed(self):
        shel = [T(date="2023-05-01", symbol=self.S, quantity=300, net_amount=6000.0, account="RRSP9"),
                T(date="2025-06-12", symbol=self.S, quantity=-100, net_amount=1000.0, account="RRSP9")]
        g = self._loss(ca(self._sell_at_loss(), sheltered_transactions=shel))
        self.assertEqual((g["disallowed_amount"], g["permanently_disallowed"]), (0.0, 0.0))

    def test_xtd_shape_only_in_window_drip_units_are_denied(self):
        # The RRSP held 5,000 before the window and acquired 8 by DRIP
        # inside it: 8 of the 100 units are denied, permanently.
        shel = [T(date="2022-01-03", symbol=self.S, quantity=5000, net_amount=50000.0, account="RRSP9"),
                T(date="2025-06-16", symbol=self.S, quantity=8, net_amount=80.0, account="RRSP9", description="DRIP")]
        g = self._loss(ca(self._sell_at_loss(), sheltered_transactions=shel))
        self.assertAlmostEqual(g["disallowed_amount"], 80.0)
        self.assertAlmostEqual(g["permanently_disallowed"], 80.0)

    def test_registered_holder_backs_at_most_what_it_still_holds(self):
        # The TFSA held 150, buys 50 inside the window and sells 180
        # before its end: it still holds 20, so at most 20 of its 50
        # acquired units are substituted property (a sale disposes of
        # the oldest units first; the least-of-three formula applied
        # per holder). Its pre-window 150 add nothing.
        shel = [T(date="2024-01-03", symbol=self.S, quantity=150, net_amount=3000.0, account="TFSA1"),
                T(date="2025-06-04", symbol=self.S, quantity=50, net_amount=500.0, account="TFSA1"),
                T(date="2025-06-24", symbol=self.S, quantity=-180, net_amount=1800.0, account="TFSA1")]
        g = self._loss(ca(self._sell_at_loss(), sheltered_transactions=shel))
        self.assertAlmostEqual(g["disallowed_amount"], 200.0)
        self.assertAlmostEqual(g["permanently_disallowed"], 200.0)

    def test_taxable_and_registered_substitutes_split_deferred_and_permanent(self):
        tax = self._sell_at_loss(
            T(date="2025-06-10", symbol=self.S, quantity=50, net_amount=500.0),
            T(date="2025-09-02", symbol=self.S, quantity=-50, net_amount=500.0))
        shel = [T(date="2025-06-11", symbol=self.S, quantity=30, net_amount=300.0, account="RRSP9")]
        g = self._loss(ca(tax, sheltered_transactions=shel))
        self.assertAlmostEqual(g["disallowed_amount"], 800.0)
        self.assertAlmostEqual(g["permanently_disallowed"], 300.0)


class TestMergerPerAccountRatio(unittest.TestCase):
    """corp_actions emits a rename-SPLIT per account at the ratio the
    broker delivered; one merger can carry two ratios in a blended book
    (audit r10). The symbol-wide pool must land on the shares actually
    delivered in total."""

    def _book(self):
        from taxjson.lib.corp_actions import CorporateAction, _emit_basis_carryover_rename
        rows = []
        for acct, disp, recv in (("55500001", 15, 15), ("55500002", 40, 41)):
            ev = CorporateAction(date="2025-07-18", time="00:00:01", action_type="merger",
                                 source_symbol="HHH.US", source_isin="", target_symbol="CCC.US",
                                 target_isin="", ratio_new=1.025, ratio_old=1.0, qty_disposed=disp,
                                 qty_received=recv, fmv=0.0, currency="USD", target_currency="USD",
                                 account=acct)
            for r in _emit_basis_carryover_rename(ev, {}, statute_note="s.85.1", cil_note="x"):
                rows.append(TaxTransaction(**r))
        self.assertEqual(sorted(r.quantity for r in rows), [1.0, 1.025])
        return ([T(date="2025-01-06", symbol="HHH.US", quantity=15, net_amount=1500.0, account="55500001"),  # pii-ok (synthetic id)
                 T(date="2025-01-06", symbol="HHH.US", quantity=40, net_amount=4000.0, account="55500002")]  # pii-ok (synthetic id)
                + rows
                + [T(date="2025-09-02", symbol="CCC.US", quantity=-15, net_amount=1800.0, account="55500001"),  # pii-ok (synthetic id)
                   T(date="2025-09-02", symbol="CCC.US", quantity=-41, net_amount=4920.0, account="55500002")])  # pii-ok (synthetic id)

    def test_blended_pool_scales_by_the_delivered_total(self):
        r = ca(self._book())
        recs = records(r)
        # 56 CCC delivered for 55 HHH at 5,500; both sold for 6,720.
        self.assertAlmostEqual(sum(g["gain"] for g in recs), 1220.0, places=6)
        self.assertAlmostEqual(sum(g["qty"] for g in recs), 56.0, places=9)
        self.assertEqual(r["inventory"], [])
        self.assertNotIn("conservation", r["_stderr"])


class TestRocOnEmptyPool(unittest.TestCase):
    """A return of capital that posts after the position was fully sold
    has no ACB to reduce: a capital gain in the year received (s.40(3)
    with a nil ACB), never a reduction of the NEXT purchase's ACB."""

    def _book(self):
        s = "XRE.TO"
        return [T(date="2025-01-06", symbol=s, quantity=100, net_amount=1000.0, currency="CAD"),
                T(date="2025-02-05", symbol=s, quantity=-100, net_amount=1100.0, currency="CAD"),
                T(action="ADJUST", date="2025-03-01", symbol=s, quantity=0.0, net_amount=-60.0,
                  currency="CAD", type="roc"),
                T(date="2026-04-01", symbol=s, quantity=50, net_amount=500.0, currency="CAD"),
                T(date="2026-06-01", symbol=s, quantity=-50, net_amount=500.0, currency="CAD")]

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
